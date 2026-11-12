//=============================================================================
// L4D1 bootstrap seam: server-side hooks into the HL2 player class.
//
// Why a hook and not a subclass yet
// --------------------------------
// The obvious first move is `class CL4DPlayer : public CHL2_Player` with
// LINK_ENTITY_TO_CLASS(player, CL4DPlayer). That is what hl2mp does, and it works
// there because hl2mp ships *both* sides: its own `DT_HL2MP_Player` send table on
// the server and a matching IMPLEMENT_CLIENTCLASS_DT on the client.
//
// A server-only player subclass cannot do that. The server sends the entity's send
// table name to the client, and the client builds the entity from the class it
// registered for that name. A new table with no client counterpart means the
// client cannot create the local player at all. So the honest order is: client and
// server classes together (ladder step 2 as written in docs/L4D1/GAMECODE.md), or
// a hook - which is this file, and which changes no netcode.
//
// What it is for
// --------------
// * It proves an L4D-specific translation unit builds into the server module and
//   is called at runtime. The console line below is the cheapest possible answer
//   to "is the module I am running the bootstrap HL2 one, or something of ours?"
// * It gives the next pieces (survivor slots, Director stub, flow distance) a
//   place to live that is not a fork of a 7,000-line file.
//
// What it must not become
// -----------------------
// A dumping ground for guessed gameplay. Numbers that belong to L4D and are not
// established go in docs/L4D1/DEVIATIONS.md with a confidence level, not here.
//=============================================================================

#ifndef L4D_SERVER_SEAM_H
#define L4D_SERVER_SEAM_H

#ifdef _WIN32
#pragma once
#endif

class CHL2_Player;

// Called at the end of CHL2_Player::Spawn, i.e. once per player per spawn, with
// everything HL2 does already done. Safe to leave empty; safe to grow.
void L4D_Server_PlayerSpawned( CHL2_Player *player );

#endif // L4D_SERVER_SEAM_H
