//========= Copyright Valve Corporation, All rights reserved. ============//
//
// Purpose: Cache for VCDs. PC async loads and uses the datacache to manage.
// 360 uses a baked resident image of aggregated compiled VCDs.
//
//=============================================================================

#include "scenefilecache/ISceneFileCache.h"
#include "filesystem.h"
#include "tier1/utldict.h"
#include "tier1/utlbuffer.h"
#include "tier1/lzmaDecoder.h"
#include "scenefilecache/SceneImageFile.h"
#include "choreoscene.h"

// memdbgon must be the last include file in a .cpp file!!!
#include "tier0/memdbgon.h"

IFileSystem	*filesystem = NULL;
																				
bool IsBufferBinaryVCD( char *pBuffer, int bufferSize )
{	
	if ( bufferSize > 4 && *(int *)pBuffer == SCENE_BINARY_TAG )
	{
		return true;	
	}

	return false;
}

//-----------------------------------------------------------------------------
// A scene image from a version this tree was not written for is worth reading if
// the bytes agree with the layout we know. L4D1 ships version 3 and its header,
// entry table and summaries are the version 2 structs - checked on device: 5877
// entries, every data range, summary and sound index inside the file. Verify that
// on the bytes in hand rather than trusting the version number, and read
// everything with memcpy so an unaligned offset cannot fault on aarch64.
//-----------------------------------------------------------------------------
static bool SceneImageLayoutIsSane( const void *pRawData, int nBytes, const SceneImageHeader_t *pHeader )
{
	if ( !pRawData || nBytes < (int)sizeof( SceneImageHeader_t ) )
		return false;

	const byte *pData = (const byte *)pRawData;
	const int nScenes = pHeader->nNumScenes;
	const int nStrings = pHeader->nNumStrings;
	const int nEntryOffset = pHeader->nSceneEntryOffset;

	if ( nScenes <= 0 || nScenes > 200000 )
		return false;
	if ( nStrings < 0 || nStrings > 1000000 )
		return false;
	if ( (long long)sizeof( SceneImageHeader_t ) + 4LL * nStrings > nBytes )
		return false;
	if ( nEntryOffset < (int)sizeof( SceneImageHeader_t ) )
		return false;
	if ( (long long)nEntryOffset + (long long)nScenes * (long long)sizeof( SceneImageEntry_t ) > nBytes )
		return false;

	// The string table is offsets from the header; every one has to point inside.
	for ( int i = 0; i < nStrings; i++ )
	{
		unsigned int nOffset = 0;
		V_memcpy( &nOffset, pData + sizeof( SceneImageHeader_t ) + 4 * i, sizeof( nOffset ) );
		if ( nOffset >= (unsigned int)nBytes )
			return false;
	}

	const int nEntriesEnd = nEntryOffset + nScenes * (int)sizeof( SceneImageEntry_t );
	for ( int i = 0; i < nScenes; i++ )
	{
		SceneImageEntry_t entry;
		V_memcpy( &entry, pData + nEntryOffset + i * (int)sizeof( SceneImageEntry_t ), sizeof( entry ) );

		if ( entry.nDataOffset < 0 || entry.nDataLength < 0 ||
			 (long long)entry.nDataOffset + (long long)entry.nDataLength > nBytes )
			return false;

		if ( entry.nSceneSummaryOffset < nEntriesEnd ||
			 (long long)entry.nSceneSummaryOffset + 8 > nBytes )
			return false;

		int nNumSounds = 0;
		V_memcpy( &nNumSounds, pData + entry.nSceneSummaryOffset + 4, sizeof( nNumSounds ) );
		if ( nNumSounds < 0 || nNumSounds > 4096 )
			return false;
		if ( (long long)entry.nSceneSummaryOffset + 8 + 4LL * nNumSounds > nBytes )
			return false;

		for ( int s = 0; s < nNumSounds; s++ )
		{
			int nSound = 0;
			V_memcpy( &nSound, pData + entry.nSceneSummaryOffset + 8 + 4 * s, sizeof( nSound ) );
			if ( nSound < 0 || nSound >= nStrings )
				return false;
		}
	}

	return true;
}

// L4D1's scene images are version 3; this SDK's constant is 2. Same layout.
#define SCENE_IMAGE_VERSION_L4D1 3

class CSceneFileCache : public CBaseAppSystem< ISceneFileCache >
{
public:
	// IAppSystem
	virtual bool			Connect( CreateInterfaceFn factory );
	virtual void			Disconnect();
	virtual InitReturnVal_t Init();
	virtual void			Shutdown();

	// ISceneFileCache
	// Physically reloads image from disk
	virtual void			Reload();

	virtual size_t			GetSceneBufferSize( char const *pFilename );
	virtual bool			GetSceneData( char const *pFilename, byte *buf, size_t bufsize );

	// alternate resident image implementation
	virtual bool			GetSceneCachedData( char const *pFilename, SceneCachedData_t *pData );
	virtual short			GetSceneCachedSound( int iScene, int iSound );
	virtual const char		*GetSceneString( short stringId );

private:
	// alternate implementation - uses a resident baked image of the file cache, contains all the compiled VCDs
	// single i/o read at startup to mount the image
	int						FindSceneInImage( const char *pSceneName );
	bool					GetSceneDataFromImage( const char *pSceneName, int iIndex, byte *pData, size_t *pLength );

private:
	CUtlBuffer						m_SceneImageFile;
};

bool CSceneFileCache::Connect( CreateInterfaceFn factory )
{
	if ( (filesystem = (IFileSystem *)factory( FILESYSTEM_INTERFACE_VERSION,NULL )) == NULL )
	{
		return false;
	}
	
	return true;
}

void CSceneFileCache::Disconnect()
{
}

InitReturnVal_t CSceneFileCache::Init()
{
	const char *pSceneImageName = IsX360() ? "scenes/scenes.360.image" : "scenes/scenes.image";

	if ( m_SceneImageFile.TellMaxPut() == 0 )
	{
		MEM_ALLOC_CREDIT();

		if ( filesystem->ReadFile( pSceneImageName, "GAME", m_SceneImageFile ) )
		{
			int nRead = m_SceneImageFile.TellMaxPut();
			if ( nRead < (int)sizeof( SceneImageHeader_t ) )
			{
				Warning( "CSceneFileCache: %s is %d bytes, too short to be a scene image. "
						 "Scenes will be loaded individually.\n", pSceneImageName, nRead );
				m_SceneImageFile.Purge();
			}
			else
			{
				SceneImageHeader_t *pHeader = (SceneImageHeader_t *)m_SceneImageFile.Base();
				bool bReadableVersion = pHeader->nVersion == SCENE_IMAGE_VERSION;
				if ( !bReadableVersion && pHeader->nVersion == SCENE_IMAGE_VERSION_L4D1 &&
					 SceneImageLayoutIsSane( m_SceneImageFile.Base(), nRead, pHeader ) )
				{
					// Reading a v3 image as v2 is the whole port: the structs are the same.
					Warning( "CSceneFileCache: %s is version %d, reading it as version %d "
							 "(layout checked entry by entry).\n", pSceneImageName,
							 pHeader->nVersion, SCENE_IMAGE_VERSION );
					bReadableVersion = true;
				}
				if ( pHeader->nId != SCENE_IMAGE_ID || !bReadableVersion )
				{
					// A scene image built by a different Source game is not worth dying
					// for. The image only makes scenes cheaper to read; every caller
					// handles "not in the image" already (GetSceneBufferSize returns 0,
					// LoadScene returns NULL), and on a phone a fatal error here is
					// indistinguishable from the port crashing. Say what the file
					// actually contains, then carry on without it.
					Warning( "CSceneFileCache: %s is not a scene image this build understands "
							 "(id 0x%08X version %d, %d bytes; expected 0x%08X version %d). "
							 "Scenes will be loaded individually.\n",
							 pSceneImageName, (unsigned int)pHeader->nId, pHeader->nVersion,
							 nRead, (unsigned int)SCENE_IMAGE_ID, SCENE_IMAGE_VERSION );
					m_SceneImageFile.Purge();
				}
			}
		}
		else
		{
			if ( IsX360() )
			{
				if ( filesystem->GetDVDMode() == DVDMODE_STRICT )
				{
					// mandatory
					Error( "CSceneFileCache: Failed to load %s\n", pSceneImageName );
				}
				else
				{
					// relaxed
					Warning( "CSceneFileCache: Failed to load %s, scene playback disabled.\n", pSceneImageName );
					return INIT_OK;
				}
			}

			m_SceneImageFile.Purge();
		}
	}

	return INIT_OK;
}

void CSceneFileCache::Shutdown()
{
	m_SceneImageFile.Purge();
}

// Physically reloads image from disk
void CSceneFileCache::Reload()
{
	Shutdown();
	Init();
}

size_t CSceneFileCache::GetSceneBufferSize( char const *pFilename )
{
	size_t returnSize = 0;

	char fn[MAX_PATH];
	Q_strncpy( fn, pFilename, sizeof( fn ) );
	Q_FixSlashes( fn );
	Q_strlower( fn );

	GetSceneDataFromImage( pFilename, FindSceneInImage( fn ), NULL, &returnSize );
	return returnSize;
}

bool CSceneFileCache::GetSceneData( char const *pFilename, byte *buf, size_t bufsize )
{
	Assert( pFilename );
	Assert( buf );
	Assert( bufsize > 0 );

	char fn[MAX_PATH];
	Q_strncpy( fn, pFilename, sizeof( fn ) );
	Q_FixSlashes( fn );
	Q_strlower( fn );

	size_t nLength = bufsize;
	return GetSceneDataFromImage( pFilename, FindSceneInImage( fn ), buf, &nLength );
}

bool CSceneFileCache::GetSceneCachedData( char const *pFilename, SceneCachedData_t *pData )
{
	int iScene = FindSceneInImage( pFilename );
	SceneImageHeader_t *pHeader = (SceneImageHeader_t *)m_SceneImageFile.Base();
	if ( !pHeader || iScene < 0 || iScene >= pHeader->nNumScenes )
	{
		// not available
		pData->sceneId = -1;
		pData->msecs = 0;
		pData->numSounds = 0;
		return false;
	}

	// get scene summary
	SceneImageEntry_t *pEntries = (SceneImageEntry_t *)( (byte *)pHeader + pHeader->nSceneEntryOffset );
	SceneImageSummary_t *pSummary = (SceneImageSummary_t *)( (byte *)pHeader + pEntries[iScene].nSceneSummaryOffset );
	
	pData->sceneId = iScene;
	pData->msecs = pSummary->msecs;
	pData->numSounds = pSummary->numSounds;

	return true;
}

short CSceneFileCache::GetSceneCachedSound( int iScene, int iSound )
{
	SceneImageHeader_t *pHeader = (SceneImageHeader_t *)m_SceneImageFile.Base();
	if ( !pHeader || iScene < 0 || iScene >= pHeader->nNumScenes )
	{
		// huh?, image file not present or bad index
		return -1;
	}

	SceneImageEntry_t *pEntries = (SceneImageEntry_t *)( (byte *)pHeader + pHeader->nSceneEntryOffset );
	SceneImageSummary_t *pSummary = (SceneImageSummary_t *)( (byte *)pHeader + pEntries[iScene].nSceneSummaryOffset );
	if ( iSound < 0 || iSound >= pSummary->numSounds )
	{
		// bad index
		Assert( 0 );
		return -1;
	}

	return pSummary->soundStrings[iSound];
}

const char *CSceneFileCache::GetSceneString( short stringId )
{
	SceneImageHeader_t *pHeader = (SceneImageHeader_t *)m_SceneImageFile.Base();
	if ( !pHeader || stringId < 0 || stringId >= pHeader->nNumStrings )
	{
		// huh?, image file not present, or index bad
		return NULL;
	}

	return pHeader->String( stringId );
}

//-----------------------------------------------------------------------------
//  Returns -1 if not found, otherwise [0..n] index.
//-----------------------------------------------------------------------------
int CSceneFileCache::FindSceneInImage( const char *pSceneName )
{
	SceneImageHeader_t *pHeader = (SceneImageHeader_t *)m_SceneImageFile.Base();
	if ( !pHeader )
	{
		return -1;
	}
	SceneImageEntry_t *pEntries = (SceneImageEntry_t *)( (byte *)pHeader + pHeader->nSceneEntryOffset );

	char szCleanName[MAX_PATH];

	V_strncpy( szCleanName, pSceneName, sizeof( szCleanName ) );
	V_strlower( szCleanName );
#ifdef POSIX
	V_FixSlashes( szCleanName, '\\' );
#else
	V_FixSlashes( szCleanName );
#endif
	V_SetExtension( szCleanName, ".vcd", sizeof( szCleanName ) );

	CRC32_t crcFilename = CRC32_ProcessSingleBuffer( szCleanName, strlen( szCleanName ) );

	// use binary search, entries are sorted by ascending crc
	int nLowerIdx = 1;
	int nUpperIdx = pHeader->nNumScenes;
	for ( ;; )
	{
		if ( nUpperIdx < nLowerIdx )
		{
			return -1;
		}
		else
		{
			int nMiddleIndex = ( nLowerIdx + nUpperIdx )/2;
			CRC32_t nProbe = pEntries[nMiddleIndex-1].crcFilename;
			if ( crcFilename < nProbe )
			{
				nUpperIdx = nMiddleIndex - 1;
			}
			else
			{
				if ( crcFilename > nProbe )
				{
					nLowerIdx = nMiddleIndex + 1;
				}
				else
				{
					return nMiddleIndex - 1;
				}
			}
		}
	}

	return -1;
}

//-----------------------------------------------------------------------------
//  Returns true if success, false otherwise. Caller must free ouput scene data
//-----------------------------------------------------------------------------
bool CSceneFileCache::GetSceneDataFromImage( const char *pFileName, int iScene, byte *pSceneData, size_t *pSceneLength )
{
	SceneImageHeader_t *pHeader = (SceneImageHeader_t *)m_SceneImageFile.Base();
	if ( !pHeader || iScene < 0 || iScene >= pHeader->nNumScenes )
	{
		if ( pSceneData )
		{
			*pSceneData = NULL;
		}
		if ( pSceneLength )
		{
			*pSceneLength = 0;
		}
		return false;
	}

	SceneImageEntry_t *pEntries = (SceneImageEntry_t *)( (byte *)pHeader + pHeader->nSceneEntryOffset );
	unsigned char *pData = (unsigned char *)pHeader + pEntries[iScene].nDataOffset;
	bool bIsCompressed;
	bIsCompressed = CLZMA::IsCompressed( pData );
	if ( bIsCompressed )
	{
		int originalSize = CLZMA::GetActualSize( pData );
		if ( pSceneData )
		{
			int nMaxLen = *pSceneLength;
			if ( originalSize <= nMaxLen )
			{
				CLZMA::Uncompress( pData, pSceneData );
			}
			else
			{
				unsigned char *pOutputData = (unsigned char *)malloc( originalSize );
				CLZMA::Uncompress( pData, pOutputData );
				V_memcpy( pSceneData, pOutputData, nMaxLen );
				free( pOutputData );
			}
		}
		if ( pSceneLength )
		{
			*pSceneLength = originalSize;
		}
	}
	else
	{
		if ( pSceneData )
		{
			size_t nCountToCopy = min(*pSceneLength, (size_t)pEntries[iScene].nDataLength );
			V_memcpy( pSceneData, pData, nCountToCopy );
		}
		if ( pSceneLength )
		{
			*pSceneLength = (size_t)pEntries[iScene].nDataLength;
		}
	}
	return true;
}

static CSceneFileCache g_SceneFileCache;
EXPOSE_SINGLE_INTERFACE_GLOBALVAR( CSceneFileCache, ISceneFileCache, SCENE_FILE_CACHE_INTERFACE_VERSION, g_SceneFileCache );
