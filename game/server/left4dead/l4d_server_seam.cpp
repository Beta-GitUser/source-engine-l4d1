//=============================================================================
// L4D1 bootstrap seam: implementation. See l4d_server_seam.h for why this is a
// hook rather than a player subclass.
//=============================================================================

#include "cbase.h"
#include "l4d_server_seam.h"
#include "hl2_player.h"
#include "player.h"
#include "world.h"

// memdbgon must be the last include file in a .cpp file.
#include "tier0/memdbgon.h"

// Announced once per map, not once per spawn: a 4-survivor server would otherwise
// repeat the same line into the log four times per round.
static bool g_bSeamAnnounced = false;

void L4D_Server_PlayerSpawned( CHL2_Player *player )
{
	if ( !player )
		return;

	if ( !g_bSeamAnnounced )
	{
		g_bSeamAnnounced = true;

		const char *mapName = "unknown";
		if ( gpGlobals->mapname != NULL_STRING )
			mapName = STRING( gpGlobals->mapname );

		// Deliberately verbose: this line is how a log tells us which module is
		// running, without needing to know anything about the build that made it.
		Msg( "L4D bootstrap: server seam active (map %s). HL2 game code, no L4D rules.\n",
			 mapName );
	}

	// Nothing is changed here yet. The next steps that belong in this function -
	// survivor slots, giving the player L4D's item loadout, informing a Director
	// stub - all need either the classname warning list from a real L4D map load
	// or a client-side counterpart class. Both are tracked in
	// docs/L4D1/GAMECODE.md; guessing them now would put invented behaviour into
	// the one place that looks authoritative.
}
