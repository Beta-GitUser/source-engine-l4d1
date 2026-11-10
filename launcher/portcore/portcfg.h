//========= Copyright Valve Corporation, All rights reserved. ============//
//
// Purpose: portable launch configuration for the Left 4 Dead port.
//
// This file must stay dependency-free (no tier0, no engine headers) so it can
// be unit tested on a workstation and reused by the Android entry point and by
// any future desktop launcher. It is the C++ twin of l4d1ctl/paths.py +
// l4d1ctl/launch.py; keep the two in sync.
//
//===========================================================================//

#ifndef PORTCORE_PORTCFG_H
#define PORTCORE_PORTCFG_H

#include <string>
#include <vector>

namespace portcore
{

// Hard ceiling that matches the launcher's LauncherArgv array.
const int kMaxArgs = 512;

// Defaults reproduce the historical hardcoded Android behaviour, so an absent
// or unreadable port.cfg changes nothing for existing users.
struct LaunchOptions
{
	std::string game = "left4dead";      // -game <name>
	std::string identity_cfg = "";       // +exec <cfg>, empty = skip
	std::string extra_args = "-novid -console -nouserclip";
	std::string player_name = "";        // informational, used in logs
	bool fullscreen = true;
	bool touch = true;
	bool nosteam = true;
	bool insecure = true;
	bool use_console = false;            // -console (also implied by extra_args)
};

// A flat KeyValues-subset document:
//
//     "key"   "value"
//     "key"   { ... }          (sections are parsed and ignored by this API)
//     key     value            (bare tokens accepted, Valve style)
//     // comment
//
// Sections are kept so a single port.cfg can grow without breaking old builds.
class Config
{
public:
	struct Node
	{
		std::string key;
		std::string value;
		std::vector<Node> children;

		bool IsSection() const { return !children.empty(); }
	};

	static bool Parse( const std::string &text, Config *out, std::string *error );
	static bool LoadFile( const std::string &path, Config *out, std::string *error );

	const Node *Find( const std::string &key ) const;

	std::string GetString( const std::string &key, const std::string &fallback = "" ) const;
	// Returns fallback when the value is present but not parseable.
	bool GetBool( const std::string &key, bool fallback ) const;
	int GetInt( const std::string &key, int fallback ) const;

	const std::vector<Node> &Nodes() const { return m_nodes; }

private:
	std::vector<Node> m_nodes;
};

// Split a command line string into tokens, honouring double quotes and
// backslash escapes (the same rules Source's CommandLine uses for +commands).
std::vector<std::string> TokenizeCommandLine( const std::string &line );

// Fill LaunchOptions from Config, leaving defaults for absent keys.
void ApplyConfig( const Config &config, LaunchOptions *options );

// Build the argv for the engine.
//
// engine_path  : argv[0], typically $APP_DATA_PATH/hl2_linux
// java_args    : extra arguments requested by the Java activity (may be empty)
// out          : receives argv; write_back_capacity is kMaxArgs
//
// Returns false (in *error) when the resulting argv would exceed kMaxArgs;
// in that case argv is still filled with as much as fits so the caller can log
// the tail that was dropped.
bool BuildLaunchArgv( const LaunchOptions &options,
                      const std::string &engine_path,
                      const std::vector<std::string> &java_args,
                      std::vector<std::string> *out,
                      std::string *error,
                      int max_args = kMaxArgs );

// Human readable, shell-ish rendering for logs. Not meant to be re-parsed.
std::string FormatArgv( const std::vector<std::string> &argv );

} // namespace portcore

#endif // PORTCORE_PORTCFG_H
