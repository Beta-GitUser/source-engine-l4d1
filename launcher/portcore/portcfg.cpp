//========= Copyright Valve Corporation, All rights reserved. ============//
// Port of the l4d1ctl KV tokenizer to dependency-free C++.
//===========================================================================//

#include "portcfg.h"

#include <cctype>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

namespace portcore
{

namespace
{

struct Token
{
	enum Kind
	{
		kAtom,
		kOpen,
		kClose,
	};

	Kind kind = kAtom;
	std::string text;
};

bool IsSpace( char c )
{
	return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' || c == '\v';
}

void UnescapeInto( const std::string &raw, std::string *out )
{
	for ( size_t i = 0; i < raw.size(); ++i )
	{
		if ( raw[i] != '\\' || i + 1 >= raw.size() )
		{
			out->push_back( raw[i] );
			continue;
		}

		const char next = raw[++i];
		switch ( next )
		{
		case 'n': out->push_back( '\n' ); break;
		case 't': out->push_back( '\t' ); break;
		case 'r': out->push_back( '\r' ); break;
		default: out->push_back( next ); break;
		}
	}
}

bool Tokenize( const std::string &text, std::vector<Token> *out, std::string *error )
{
	size_t i = 0;
	const size_t size = text.size();

	// Skip a UTF-8 BOM if present.
	if ( size >= 3 && static_cast<unsigned char>( text[0] ) == 0xEF &&
	     static_cast<unsigned char>( text[1] ) == 0xBB &&
	     static_cast<unsigned char>( text[2] ) == 0xBF )
	{
		i = 3;
	}

	while ( i < size )
	{
		const char c = text[i];

		if ( IsSpace( c ) )
		{
			++i;
			continue;
		}

		// Line comments.
		if ( c == '/' && i + 1 < size && text[i + 1] == '/' )
		{
			while ( i < size && text[i] != '\n' )
				++i;
			continue;
		}

		if ( c == '{' )
		{
			Token token;
			token.kind = Token::kOpen;
			out->push_back( token );
			++i;
			continue;
		}

		if ( c == '}' )
		{
			Token token;
			token.kind = Token::kClose;
			out->push_back( token );
			++i;
			continue;
		}

		if ( c == '"' )
		{
			++i;
			std::string raw;
			bool closed = false;
			while ( i < size )
			{
				const char ch = text[i++];
				if ( ch == '\\' && i < size )
				{
					raw.push_back( ch );
					raw.push_back( text[i++] );
					continue;
				}
				if ( ch == '"' )
				{
					closed = true;
					break;
				}
				raw.push_back( ch );
			}
			if ( !closed )
			{
				if ( error )
					*error = "unterminated quoted string";
				return false;
			}

			Token token;
			token.kind = Token::kAtom;
			UnescapeInto( raw, &token.text );
			out->push_back( token );
			continue;
		}

		// Bare token.
		std::string bare;
		while ( i < size && !IsSpace( text[i] ) && text[i] != '{' && text[i] != '}' &&
		        text[i] != '"' )
		{
			bare.push_back( text[i++] );
		}
		Token token;
		token.kind = Token::kAtom;
		token.text = bare;
		out->push_back( token );
	}
	return true;
}

bool IsPlatformToken( const std::string &text )
{
	if ( text.size() < 3 || text.front() != '[' || text.back() != ']' )
		return false;
	return true;
}

bool ParseBlock( const std::vector<Token> &tokens, size_t *pos, bool top,
                 std::vector<Config::Node> *out, std::string *error )
{
	while ( *pos < tokens.size() )
	{
		const Token &token = tokens[*pos];

		if ( token.kind == Token::kClose )
		{
			++(*pos);
			if ( top )
				continue; // stray brace at the top level: ignore
			return true;
		}

		if ( token.kind == Token::kOpen )
		{
			++(*pos); // stray open brace: ignore
			continue;
		}

		Config::Node node;
		node.key = token.text;
		++(*pos);

		if ( *pos < tokens.size() && tokens[*pos].kind == Token::kOpen )
		{
			++(*pos);
			if ( !ParseBlock( tokens, pos, false, &node.children, error ) )
				return false;
		}
		else if ( *pos < tokens.size() && tokens[*pos].kind == Token::kAtom )
		{
			node.value = tokens[*pos].text;
			++(*pos);
			if ( *pos < tokens.size() && tokens[*pos].kind == Token::kAtom &&
			     IsPlatformToken( tokens[*pos].text ) )
			{
				++(*pos); // [$WIN32] and friends: parsed, not applied
			}
		}

		out->push_back( node );
	}

	if ( !top )
	{
		if ( error )
			*error = "unexpected end of file: unbalanced '{'";
		return false;
	}
	return true;
}

std::string ToLower( const std::string &value )
{
	std::string out = value;
	for ( size_t i = 0; i < out.size(); ++i )
		out[i] = static_cast<char>( std::tolower( static_cast<unsigned char>( out[i] ) ) );
	return out;
}

} // namespace

bool Config::Parse( const std::string &text, Config *out, std::string *error )
{
	if ( !out )
		return false;

	std::vector<Token> tokens;
	if ( !Tokenize( text, &tokens, error ) )
		return false;

	out->m_nodes.clear();
	size_t pos = 0;
	return ParseBlock( tokens, &pos, true, &out->m_nodes, error );
}

bool Config::LoadFile( const std::string &path, Config *out, std::string *error )
{
	std::ifstream stream( path.c_str(), std::ios::binary );
	if ( !stream.is_open() )
	{
		if ( error )
			*error = "cannot open " + path;
		return false;
	}

	std::ostringstream buffer;
	buffer << stream.rdbuf();
	return Parse( buffer.str(), out, error );
}

namespace
{

const Config::Node *FindIn( const std::vector<Config::Node> &nodes, const std::string &lowered )
{
	// Breadth-first so a top-level key always wins over a nested duplicate.
	for ( const Config::Node &node : nodes )
	{
		if ( ToLower( node.key ) == lowered )
			return &node;
	}
	for ( const Config::Node &node : nodes )
	{
		const Config::Node *found = FindIn( node.children, lowered );
		if ( found != nullptr )
			return found;
	}
	return nullptr;
}

} // namespace

const Config::Node *Config::Find( const std::string &key ) const
{
	// Accept both a flat port.cfg and one wrapped in a section, which is what
	// people naturally write when they copy a cfg snippet.
	return FindIn( m_nodes, ToLower( key ) );
}

std::string Config::GetString( const std::string &key, const std::string &fallback ) const
{
	const Node *node = Find( key );
	if ( node == nullptr || node->IsSection() )
		return fallback;
	return node->value;
}

bool Config::GetBool( const std::string &key, bool fallback ) const
{
	const Node *node = Find( key );
	if ( node == nullptr || node->IsSection() )
		return fallback;

	const std::string value = ToLower( node->value );
	if ( value.empty() )
		return fallback;
	if ( value == "0" || value == "false" || value == "no" || value == "off" )
		return false;
	return true;
}

int Config::GetInt( const std::string &key, int fallback ) const
{
	const Node *node = Find( key );
	if ( node == nullptr || node->IsSection() )
		return fallback;
	try
	{
		return std::stoi( node->value, nullptr, 0 );
	}
	catch ( ... )
	{
		return fallback;
	}
}

std::vector<std::string> TokenizeCommandLine( const std::string &line )
{
	std::vector<std::string> out;
	size_t i = 0;
	const size_t size = line.size();

	while ( i < size )
	{
		while ( i < size && IsSpace( line[i] ) )
			++i;
		if ( i >= size )
			break;

		std::string token;
		char quote = 0;
		if ( line[i] == '"' || line[i] == '\'' )
			quote = line[i++];

		while ( i < size )
		{
			const char c = line[i];
			if ( quote != 0 )
			{
				if ( c == quote )
				{
					++i;
					break;
				}
			}
			else if ( IsSpace( c ) )
			{
				break;
			}

			if ( c == '\\' && i + 1 < size )
			{
				token.push_back( line[i + 1] );
				i += 2;
				continue;
			}

			token.push_back( c );
			++i;
		}

		if ( !token.empty() )
			out.push_back( token );
	}
	return out;
}

void ApplyConfig( const Config &config, LaunchOptions *options )
{
	if ( !options )
		return;

	options->game = config.GetString( "game", options->game );
	options->identity_cfg = config.GetString( "identity_cfg", options->identity_cfg );
	options->extra_args = config.GetString( "extra_args", options->extra_args );
	options->player_name = config.GetString( "player_name", options->player_name );
	options->fullscreen = config.GetBool( "fullscreen", options->fullscreen );
	options->touch = config.GetBool( "touch", options->touch );
	options->nosteam = config.GetBool( "nosteam", options->nosteam );
	options->insecure = config.GetBool( "insecure", options->insecure );
	options->use_console = config.GetBool( "console", options->use_console );
}

bool BuildLaunchArgv( const LaunchOptions &options,
                      const std::string &engine_path,
                      const std::vector<std::string> &java_args,
                      std::vector<std::string> *out,
                      std::string *error,
                      int max_args )
{
	if ( out == nullptr )
		return false;

	out->clear();
	int dropped = 0;

	auto push = [&]( const std::string &arg ) {
		if ( static_cast<int>( out->size() ) + 1 >= max_args )
		{
			++dropped;
			return;
		}
		out->push_back( arg );
	};

	push( engine_path );

	for ( const std::string &arg : TokenizeCommandLine( options.extra_args ) )
		push( arg );

	if ( !options.game.empty() )
	{
		push( "-game" );
		push( options.game );
	}

	for ( const std::string &arg : java_args )
		push( arg );

	push( options.fullscreen ? "-fullscreen" : "-windowed" );
	if ( options.touch )
		push( "-touch" );
	if ( options.use_console )
		push( "-console" );
	if ( options.nosteam )
		push( "-nosteam" );
	if ( options.insecure )
		push( "-insecure" );

	if ( !options.identity_cfg.empty() )
	{
		push( "+exec" );
		push( options.identity_cfg );
	}

	if ( dropped > 0 )
	{
		if ( error )
		{
			char buffer[128];
			snprintf( buffer, sizeof( buffer ), "%d argument(s) dropped: argv limit is %d",
			               dropped, max_args );
			*error = buffer;
		}
		return false;
	}

	if ( error )
		error->clear();
	return true;
}

std::string FormatArgv( const std::vector<std::string> &argv )
{
	std::string out;
	for ( size_t i = 0; i < argv.size(); ++i )
	{
		if ( i )
			out += ' ';

		const std::string &arg = argv[i];
		bool needs_quotes = arg.empty();
		for ( size_t c = 0; c < arg.size() && !needs_quotes; ++c )
			needs_quotes = IsSpace( arg[c] ) || arg[c] == '"';

		if ( !needs_quotes )
		{
			out += arg;
			continue;
		}

		out += '"';
		for ( size_t c = 0; c < arg.size(); ++c )
		{
			if ( arg[c] == '"' || arg[c] == '\\' )
				out += '\\';
			out += arg[c];
		}
		out += '"';
	}
	return out;
}

} // namespace portcore
