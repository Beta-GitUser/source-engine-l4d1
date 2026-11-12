/*
Copyright (C) 2022 nillerusr

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of 
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
*/

#include <stdint.h> // intptr_t, used by the debug probe's handle marshalling
#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
#include <jni.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#include <SDL_hints.h>
#include "tier0/dbg.h"
#include "tier0/threadtools.h"
#include "portcore/portcfg.h"

char *LauncherArgv[512];
char java_args[4096];
int iLastArgs = 0;

extern void InitCrashHandler();
DLL_EXPORT int LauncherMain( int argc, char **argv ); // from launcher.cpp

DLL_EXPORT int Java_com_valvesoftware_ValveActivity2_setenv(JNIEnv *jenv, jclass *jclass, jstring env, jstring value, jint over)
{
	Msg( "Java_com_valvesoftware_ValveActivity2_setenv %s=%s\n", jenv->GetStringUTFChars(env, NULL), jenv->GetStringUTFChars(value, NULL) );
	return setenv( jenv->GetStringUTFChars(env, NULL), jenv->GetStringUTFChars(value, NULL), over );
}

DLL_EXPORT void Java_com_valvesoftware_ValveActivity2_nativeOnActivityResult()
{
//	Msg( "Java_com_valvesoftware_ValveActivity_nativeOnActivityResult\n" );
}

DLL_EXPORT void Java_com_valvesoftware_ValveActivity2_setArgs(JNIEnv *env, jclass *clazz, jstring str)
{
	strncpy( java_args, env->GetStringUTFChars(str, NULL), sizeof( java_args ) - 1 );
	java_args[sizeof( java_args ) - 1] = '\0';
}

// ---------------------------------------------------------------------------
// Debug probe used by launcher/android/apk (ValveActivity2).
//
// Each probe runs in a forked child and the parent reads back a verdict. The
// reason is that the interesting failure is a segfault inside a library's static
// initialisers: loading the engine modules on a device where the engine has never
// run is exactly the situation where a crash is likely, and a crash in the UI
// process takes the report with it. Surviving is what makes the report readable.
//
// The child does dlopen/dlsym/CreateInterface, writes one line and _exit()s. It
// calls no JNI and touches no C++ objects, because it is a fork of an ART process
// and half of that process's state is not meant to be used from here.
//
// alarm() in the child turns a hang into SIGALRM, which the parent reports like
// any other crash instead of waiting forever.
// ---------------------------------------------------------------------------

typedef void *( *CreateInterfaceFn )( const char *name, int *returnCode );

static const int PROBE_TIMEOUT_SECONDS = 20;

// Runs in the child. Async-signal-safety matters: write() and _exit() only, no
// printf, no allocation, no Java.
static void ProbeInChild( int outFd, const char *path, const char *interfaceName )
{
	char message[1024];

	dlerror();
	void *library = dlopen( path, RTLD_NOW | RTLD_GLOBAL );
	if ( !library )
	{
		const char *error = dlerror();
		snprintf( message, sizeof message, "dlopen failed: %s", error ? error : "unknown" );
		write( outFd, message, strlen( message ) );
		_exit( 2 );
	}

	if ( !interfaceName || !interfaceName[0] )
	{
		write( outFd, "loaded", 6 );
		_exit( 0 );
	}

	CreateInterfaceFn factory = ( CreateInterfaceFn )dlsym( library, "CreateInterface" );
	if ( !factory )
	{
		snprintf( message, sizeof message, "no CreateInterface export in the module" );
		write( outFd, message, strlen( message ) );
		_exit( 3 );
	}

	int returnCode = 0;
	void *iface = factory( interfaceName, &returnCode );
	if ( !iface )
	{
		snprintf( message, sizeof message, "CreateInterface returned null (code %d)", returnCode );
		write( outFd, message, strlen( message ) );
		_exit( 4 );
	}

	snprintf( message, sizeof message, "interface at %p (code %d)", iface, returnCode );
	write( outFd, message, strlen( message ) );
	_exit( 0 );
}

DLL_EXPORT jstring Java_com_valvesoftware_ValveActivity2_nativeProbeLibrary( JNIEnv *env, jclass *clazz,
																				 jstring path, jstring interfaceName )
{
	const char *libraryPath = env->GetStringUTFChars( path, NULL );
	const char *name = interfaceName ? env->GetStringUTFChars( interfaceName, NULL ) : NULL;

	int fds[2];
	if ( pipe( fds ) != 0 )
	{
		env->ReleaseStringUTFChars( path, libraryPath );
		if ( name )
			env->ReleaseStringUTFChars( interfaceName, name );
		return env->NewStringUTF( "could not create a pipe" );
	}

	pid_t child = fork();
	if ( child < 0 )
	{
		close( fds[0] );
		close( fds[1] );
		env->ReleaseStringUTFChars( path, libraryPath );
		if ( name )
			env->ReleaseStringUTFChars( interfaceName, name );
		return env->NewStringUTF( "could not fork" );
	}

	if ( child == 0 )
	{
		close( fds[0] );
		alarm( PROBE_TIMEOUT_SECONDS );
		ProbeInChild( fds[1], libraryPath, name );
		_exit( 1 );
	}

	close( fds[1] );

	char childMessage[1024];
	ssize_t length = read( fds[0], childMessage, sizeof childMessage - 1 );
	close( fds[0] );
	childMessage[length > 0 ? ( int )length : 0] = '\0';

	int status = 0;
	waitpid( child, &status, 0 );

	const char *phase = ( name && name[0] ) ? "CreateInterface" : "dlopen";

	char report[1600];
	if ( WIFSIGNALED( status ) )
	{
		const int signalNumber = WTERMSIG( status );
		const char *signalName = "signal";
		if ( signalNumber == 11 ) signalName = "SIGSEGV";
		else if ( signalNumber == 6 ) signalName = "SIGABRT";
		else if ( signalNumber == 4 ) signalName = "SIGILL";
		else if ( signalNumber == 8 ) signalName = "SIGFPE";
		else if ( signalNumber == 14 ) signalName = "SIGALRM (timed out)";
		snprintf( report, sizeof report, "CRASHED in %s: %s (%d)", phase, signalName, signalNumber );
	}
	else if ( WEXITSTATUS( status ) == 0 )
	{
		snprintf( report, sizeof report, "ok: %s", childMessage );
	}
	else
	{
		snprintf( report, sizeof report, "failed in %s (exit %d): %s", phase, WEXITSTATUS( status ), childMessage );
	}

	env->ReleaseStringUTFChars( path, libraryPath );
	if ( name )
		env->ReleaseStringUTFChars( interfaceName, name );

	return env->NewStringUTF( report );
}

// Builds the engine argv from $APP_DATA_PATH/port.cfg (see launcher/portcore).
// With no config file this reproduces the historical hardcoded behaviour, so
// existing installs keep booting exactly as before.
void SetLauncherArgs()
{
	const char *appData = getenv( "APP_DATA_PATH" );

	static char binPath[2048];
	snprintf( binPath, sizeof binPath, "%s/hl2_linux", appData ? appData : "." );

	portcore::LaunchOptions options;

	if ( appData )
	{
		char configPath[2048];
		snprintf( configPath, sizeof configPath, "%s/port.cfg", appData );

		portcore::Config config;
		std::string configError;
		if ( portcore::Config::LoadFile( configPath, &config, &configError ) )
		{
			portcore::ApplyConfig( config, &options );
			Msg( "launcher: using %s\n", configPath );
		}
		else
		{
			Msg( "launcher: %s not used (%s); defaults apply\n", configPath, configError.c_str() );
		}
	}
	else
	{
		Msg( "launcher: APP_DATA_PATH is not set; defaults apply\n" );
	}

	// Arguments handed over by the Java activity, e.g. "+map c1m1_hotel".
	const std::vector<std::string> javaArgs = portcore::TokenizeCommandLine( java_args );

	std::vector<std::string> argv;
	std::string argvError;
	if ( !portcore::BuildLaunchArgv( options, binPath, javaArgs, &argv, &argvError ) )
		Warning( "launcher: %s\n", argvError.c_str() );

	iLastArgs = 0;
	for ( size_t i = 0; i < argv.size() && iLastArgs < ( sizeof( LauncherArgv ) / sizeof( LauncherArgv[0] ) ) - 1; ++i )
	{
		// LauncherArgv outlives this function; the engine only reads it.
		LauncherArgv[iLastArgs++] = strdup( argv[i].c_str() );
	}

	Msg( "launcher: %s\n", portcore::FormatArgv( argv ).c_str() );
}

float GetTotalMemory()
{
	int64_t mem = 0;

	char meminfo[8196] = { 0 };
	FILE *f = fopen("/proc/meminfo", "r");
	if( !f )
		return 0.f;

	size_t size = fread(meminfo, 1, sizeof(meminfo), f);
	if( !size )
		return 0.f;

	char *s = strstr(meminfo, "MemTotal:");

	if( !s ) return 0.f;

	sscanf(s+9, "%lld", &mem);
	fclose(f);

	return mem/1024/1024.f;
}

void android_property_print(const char *name)
{
	char prop[1024];

	char strValue[64];
	memset (strValue, 0, 64);
	snprintf(prop, sizeof(prop), "getprop %s", name);
	FILE *fp = NULL;
	fp = popen(prop, "r");
	if (!fp) return;

	fgets(strValue, sizeof(strValue), fp);
	pclose(fp);
	fp = NULL;

	Msg("prop %s=%s", name, strValue);
}


DLL_EXPORT int LauncherMainAndroid( int argc, char **argv )
{
	InitCrashHandler();

	Msg("GetTotalMemory() = %.2f \n", GetTotalMemory());

	android_property_print("ro.build.version.sdk");
	android_property_print("ro.product.device");
	android_property_print("ro.product.manufacturer");
	android_property_print("ro.product.model");
	android_property_print("ro.product.name");

	SetLauncherArgs();

	SDL_SetHint(SDL_HINT_TOUCH_MOUSE_EVENTS, "0");
	DeclareCurrentThreadIsMainThread(); // Init thread propertly on Android

	return LauncherMain(iLastArgs, LauncherArgv);
}
