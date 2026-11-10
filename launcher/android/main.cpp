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

#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
#include <jni.h>
#include <stdlib.h>
#include <string.h>
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
