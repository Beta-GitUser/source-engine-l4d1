// Minimal assert-based tests for launcher/portcore.
//
// Build and run with scripts/test-portcore.sh (no engine dependencies).

#include "../portcfg.h"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <vector>

namespace
{

int g_failures = 0;
int g_checks = 0;

#define CHECK(condition)                                                            \
	do                                                                              \
	{                                                                               \
		++g_checks;                                                                  \
		if ( !( condition ) )                                                        \
		{                                                                            \
			++g_failures;                                                            \
			std::printf( "FAIL %s:%d  %s\n", __FILE__, __LINE__, #condition );        \
		}                                                                            \
	} while ( 0 )

#define CHECK_EQ(actual, expected)                                                   \
	do                                                                               \
	{                                                                                \
		++g_checks;                                                                   \
		if ( !( ( actual ) == ( expected ) ) )                                        \
		{                                                                             \
			++g_failures;                                                             \
			std::printf( "FAIL %s:%d  %s == %s\n", __FILE__, __LINE__, #actual,        \
			             #expected );                                                  \
		}                                                                             \
	} while ( 0 )

bool Contains( const std::vector<std::string> &argv, const std::string &value )
{
	for ( const std::string &arg : argv )
	{
		if ( arg == value )
			return true;
	}
	return false;
}

int IndexOf( const std::vector<std::string> &argv, const std::string &value )
{
	for ( size_t i = 0; i < argv.size(); ++i )
	{
		if ( argv[i] == value )
			return static_cast<int>( i );
	}
	return -1;
}

void TestDefaults()
{
	portcore::LaunchOptions options;
	CHECK_EQ( options.game, std::string( "left4dead" ) );
	CHECK( options.fullscreen );
	CHECK( options.nosteam );
	CHECK( options.insecure );
	CHECK( options.touch );
	CHECK_EQ( options.identity_cfg, std::string( "" ) );
}

void TestTokenizer()
{
	using portcore::TokenizeCommandLine;

	auto tokens = TokenizeCommandLine( "-novid -console   -nouserclip" );
	CHECK_EQ( tokens.size(), size_t( 3 ) );
	CHECK_EQ( tokens[2], std::string( "-nouserclip" ) );

	tokens = TokenizeCommandLine( "-game \"Left 4 Dead\" +exec a.cfg" );
	CHECK_EQ( tokens.size(), size_t( 4 ) );
	CHECK_EQ( tokens[1], std::string( "Left 4 Dead" ) );

	tokens = TokenizeCommandLine( "\"quoted \\\"inner\\\"\" plain" );
	CHECK_EQ( tokens.size(), size_t( 2 ) );
	CHECK_EQ( tokens[0], std::string( "quoted \"inner\"" ) );

	tokens = TokenizeCommandLine( "single 'two words'" );
	CHECK_EQ( tokens.size(), size_t( 2 ) );
	CHECK_EQ( tokens[1], std::string( "two words" ) );

	CHECK( TokenizeCommandLine( "   " ).empty() );
}

void TestConfigParsing()
{
	const std::string text =
	    "// port configuration\n"
	    "\"port\"\n"
	    "{\n"
	    "\tgame\t\t\"left4dead\"\n"
	    "\tfullscreen\t\"0\"\n"
	    "\ttouch\t\t\"1\"\n"
	    "\tnosteam\t\t\"1\"\n"
	    "\tidentity_cfg\t\"l4d1_identity.cfg\"\n"
	    "\textra_args\t\"-novid -dev\"\n"
	    "\tplayer_name\t\"Zoey Fan\"\n"
	    "\tignored_section { \"a\" \"b\" }\n"
	    "}\n"
	    "\"top_level\"\t\"value\"\n";

	portcore::Config config;
	std::string error;
	CHECK( portcore::Config::Parse( text, &config, &error ) );
	CHECK_EQ( error, std::string( "" ) );

	portcore::LaunchOptions options;
	portcore::ApplyConfig( config, &options );

	CHECK_EQ( options.game, std::string( "left4dead" ) );
	CHECK( !options.fullscreen );
	CHECK( options.touch );
	CHECK_EQ( options.identity_cfg, std::string( "l4d1_identity.cfg" ) );
	CHECK_EQ( options.extra_args, std::string( "-novid -dev" ) );
	CHECK_EQ( options.player_name, std::string( "Zoey Fan" ) );

	CHECK_EQ( config.GetString( "PLAYER_NAME" ), std::string( "Zoey Fan" ) );
	CHECK_EQ( config.GetString( "missing", "fallback" ), std::string( "fallback" ) );
	CHECK( config.GetBool( "missing", true ) );
	CHECK_EQ( config.GetInt( "missing", 7 ), 7 );

	// Sections are not values.
	CHECK( config.Find( "ignored_section" ) != nullptr );
	CHECK_EQ( config.GetString( "ignored_section", "x" ), std::string( "x" ) );
}

void TestConfigFlatAndBare()
{
	const std::string text =
	    "game left4dead\n"
	    "fullscreen 0 [$WIN32]\n"
	    "insecure 0\n";

	portcore::Config config;
	std::string error;
	CHECK( portcore::Config::Parse( text, &config, &error ) );

	portcore::LaunchOptions options;
	portcore::ApplyConfig( config, &options );
	CHECK_EQ( options.game, std::string( "left4dead" ) );
	CHECK( !options.fullscreen );
	CHECK( !options.insecure );
}

void TestConfigErrors()
{
	portcore::Config config;
	std::string error;
	CHECK( !portcore::Config::Parse( "\"a\" { \"b\" \"c\"", &config, &error ) );
	CHECK( !error.empty() );

	CHECK( !portcore::Config::Parse( "\"a\" \"unterminated", &config, &error ) );

	CHECK( !portcore::Config::LoadFile( "/nonexistent/port.cfg", &config, &error ) );
	CHECK( !error.empty() );
}

void TestLoadFile()
{
	const char *path = "/tmp/portcore_test_port.cfg";
	{
		std::ofstream stream( path );
		stream << "\"game\" \"hl2\"\n\"touch\" \"0\"\n";
	}

	portcore::Config config;
	std::string error;
	CHECK( portcore::Config::LoadFile( path, &config, &error ) );

	portcore::LaunchOptions options;
	portcore::ApplyConfig( config, &options );
	CHECK_EQ( options.game, std::string( "hl2" ) );
	CHECK( !options.touch );

	std::remove( path );
}

void TestArgvDefaults()
{
	portcore::LaunchOptions options;
	std::vector<std::string> argv;
	std::string error;
	CHECK( portcore::BuildLaunchArgv( options, "/data/hl2_linux", {}, &argv, &error ) );

	CHECK( !argv.empty() );
	CHECK_EQ( argv[0], std::string( "/data/hl2_linux" ) );
	CHECK( Contains( argv, "-nouserclip" ) );
	CHECK( Contains( argv, "-game" ) );
	CHECK_EQ( argv[IndexOf( argv, "-game" ) + 1], std::string( "left4dead" ) );
	CHECK( Contains( argv, "-fullscreen" ) );
	CHECK( Contains( argv, "-touch" ) );
	CHECK( Contains( argv, "-nosteam" ) );
	CHECK( Contains( argv, "-insecure" ) );
	CHECK( !Contains( argv, "+exec" ) );
	CHECK_EQ( error, std::string( "" ) );
}

void TestArgvWithIdentityAndJavaArgs()
{
	portcore::LaunchOptions options;
	options.identity_cfg = "l4d1_identity.cfg";
	options.fullscreen = false;
	options.extra_args = "-novid";
	options.use_console = true;

	std::vector<std::string> argv;
	std::string error;
	CHECK( portcore::BuildLaunchArgv( options, "engine.so", { "+map", "c1m1_hotel" }, &argv, &error ) );

	CHECK( Contains( argv, "-windowed" ) );
	CHECK( !Contains( argv, "-fullscreen" ) );
	CHECK( Contains( argv, "-console" ) );
	CHECK( Contains( argv, "+exec" ) );
	CHECK_EQ( argv[IndexOf( argv, "+exec" ) + 1], std::string( "l4d1_identity.cfg" ) );
	CHECK( Contains( argv, "c1m1_hotel" ) );

	// Java args come before the trailing flags, and are not duplicated.
	CHECK( IndexOf( argv, "+map" ) < IndexOf( argv, "-windowed" ) );
}

void TestArgvLimit()
{
	portcore::LaunchOptions options;
	std::vector<std::string> argv;
	std::string error;
	std::vector<std::string> many( portcore::kMaxArgs * 2, "-x" );
	CHECK( !portcore::BuildLaunchArgv( options, "engine", many, &argv, &error ) );
	CHECK( !error.empty() );
	CHECK( static_cast<int>( argv.size() ) < portcore::kMaxArgs );
}

void TestFormatArgv()
{
	portcore::LaunchOptions options;
	std::vector<std::string> argv;
	std::string error;
	portcore::BuildLaunchArgv( options, "/data/hl2_linux", { "a b" }, &argv, &error );

	const std::string formatted = portcore::FormatArgv( argv );
	CHECK( formatted.find( "\"a b\"" ) != std::string::npos );
	CHECK( formatted.find( "/data/hl2_linux" ) != std::string::npos );
}

void TestArgvIsNullSafe()
{
	portcore::LaunchOptions options;
	std::string error;
	CHECK( !portcore::BuildLaunchArgv( options, "engine", {}, nullptr, &error ) );
}

} // namespace

int main()
{
	TestDefaults();
	TestTokenizer();
	TestConfigParsing();
	TestConfigFlatAndBare();
	TestConfigErrors();
	TestLoadFile();
	TestArgvDefaults();
	TestArgvWithIdentityAndJavaArgs();
	TestArgvLimit();
	TestFormatArgv();
	TestArgvIsNullSafe();

	if ( g_failures == 0 )
	{
		std::printf( "portcore: %d checks passed\n", g_checks );
		return 0;
	}
	std::printf( "portcore: %d/%d checks failed\n", g_failures, g_checks );
	return 1;
}
