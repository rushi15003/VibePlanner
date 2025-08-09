import asyncio
import os
from typing import Annotated
from dotenv import load_dotenv
from fastmcp import FastMCP

# Fix import path - adjust based on your actual FastMCP version
try:
    from fastmcp.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
except ImportError:
    from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair

# Use FastMCP's error classes if available, fallback to mcp
try:
    from fastmcp import ErrorData, McpError
    from fastmcp.types import INVALID_PARAMS, INTERNAL_ERROR
except ImportError:
    from mcp import ErrorData, McpError
    from mcp.types import INVALID_PARAMS, INTERNAL_ERROR

from mcp.server.auth.provider import AccessToken
from pydantic import Field
import httpx
from urllib.parse import quote_plus

load_dotenv()

# ===== env / assertions =====
TOKEN = os.environ.get("AUTH_TOKEN")
MY_NUMBER = os.environ.get("MY_NUMBER")

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
OMDB_API_KEY = os.environ.get("OMDB_API_KEY")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

assert TOKEN is not None, "Please set AUTH_TOKEN in your .env file"
assert MY_NUMBER is not None, "Please set MY_NUMBER in your .env file"

print(f"Server starting with AUTH_TOKEN: {TOKEN[:8]}...")

# ===== Simplified Auth Provider =====
class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key, jwks_uri=None, issuer=None, audience=None)
        self.expected_token = token
        print(f"Auth provider initialized with token: {token[:8]}...")

    async def load_access_token(self, token: str) -> AccessToken | None:
        print(f"Validating token: '{token[:8]}...' against expected: '{self.expected_token[:8]}...'")
        
        if token == self.expected_token:
            print("✓ Token validation successful")
            return AccessToken(
                token=token,
                client_id="vibe-planner-client",
                scopes=["*"],
                expires_at=None,
            )
        else:
            print("❌ Token validation failed")
            return None

# ===== FastMCP server instance with NO AUTH temporarily =====
# Let's try without authentication first to see if that fixes the session issues
print("Creating FastMCP server WITHOUT auth to test...")
mcp = FastMCP("Vibe Planner MCP Server")

# If you want to re-enable auth later, uncomment this:
# mcp = FastMCP("Vibe Planner MCP Server", auth=SimpleBearerAuthProvider(TOKEN))

# ===== validate tool (required by Puch) =====
@mcp.tool
async def validate() -> str:
    print("validate() called")
    return MY_NUMBER

# ===== Your existing helper functions (unchanged) =====
async def fetch_spotify_token() -> str | None:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    token_url = "https://accounts.spotify.com/api/token"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                token_url,
                data={"grant_type": "client_credentials"},
                auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
                timeout=20,
            )
            resp.raise_for_status()
            
            try:
                json_data = resp.json()
                if json_data is None:
                    raise McpError(ErrorData(code=INTERNAL_ERROR, message="Spotify returned empty response"))
                return json_data.get("access_token")
            except ValueError as json_error:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Invalid JSON from Spotify: {json_error}"))
            
        except httpx.HTTPError as http_error:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Spotify HTTP error: {http_error}"))
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Spotify token error: {e}"))

async def fetch_spotify_playlists(vibe: str, limit: int = 5):
    token = await fetch_spotify_token()
    if not token:
        return []
    q = quote_plus(vibe)
    url = f"https://api.spotify.com/v1/search?q={q}&type=playlist&limit={limit}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            
            try:
                data = r.json()
                if data is None:
                    return []
                
                items = data.get("playlists", {}).get("items", [])
                out = []
                for p in items:
                    if p:
                        out.append({
                            "name": p.get("name", "Unknown"),
                            "link": p.get("external_urls", {}).get("spotify", ""),
                            "image": (p.get("images") or [{}])[0].get("url", "")
                        })
                return out
            except ValueError as json_error:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Invalid JSON from Spotify search: {json_error}"))
                
        except httpx.HTTPError as http_error:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Spotify HTTP error: {http_error}"))
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Spotify fetch error: {e}"))

async def fetch_youtube_recipes(vibe: str, limit: int = 5):
    if not YOUTUBE_API_KEY:
        return []
    q = quote_plus(f"{vibe} recipe")
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={q}&type=video&maxResults={limit}&key={YOUTUBE_API_KEY}"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, timeout=20)
            r.raise_for_status()
            
            try:
                data = r.json()
                if data is None:
                    return []
                    
                items = data.get("items", [])
                out = []
                for it in items:
                    if it:
                        vid_id = it.get("id", {}).get("videoId")
                        title = it.get("snippet", {}).get("title")
                        if vid_id and title:
                            out.append({"title": title, "link": f"https://www.youtube.com/watch?v={vid_id}"})
                return out
            except ValueError as json_error:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Invalid JSON from YouTube: {json_error}"))
                
        except httpx.HTTPError as http_error:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"YouTube HTTP error: {http_error}"))
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"YouTube fetch error: {e}"))

async def fetch_google_books(vibe: str, limit: int = 5):
    q = quote_plus(vibe)
    url = f"https://www.googleapis.com/books/v1/volumes?q={q}&maxResults={limit}"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, timeout=20)
            r.raise_for_status()
            items = r.json().get("items", [])
            out = []
            for it in items:
                info = it.get("volumeInfo", {})
                out.append({
                    "title": info.get("title"),
                    "authors": info.get("authors", []),
                    "link": info.get("infoLink")
                })
            return out
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Google Books fetch error: {e}"))

async def fetch_omdb_movies(vibe: str, limit: int = 5):
    if not OMDB_API_KEY:
        print(f"OMDb: No API key provided")
        return []
    
    search_terms = [vibe, f"{vibe} movie"]
    vibe_lower = vibe.lower()
    if "cozy" in vibe_lower or "rainy" in vibe_lower:
        search_terms.extend(["romantic comedy", "drama"])
    elif "adventure" in vibe_lower or "exciting" in vibe_lower:
        search_terms.extend(["action", "adventure"])
    elif "scary" in vibe_lower or "spooky" in vibe_lower:
        search_terms.append("horror")
    elif "funny" in vibe_lower or "comedy" in vibe_lower:
        search_terms.append("comedy")
    
    for search_term in search_terms:
        q = quote_plus(search_term)
        url = f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&s={q}"
        
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, timeout=20)
                r.raise_for_status()
                
                try:
                    data = r.json()
                    if data is None:
                        continue
                    
                    if data.get("Response") == "False":
                        continue
                        
                    results = data.get("Search", [])[:limit]
                    if results:
                        out = []
                        for m in results:
                            if m:
                                out.append({
                                    "title": m.get("Title", "Unknown Title"),
                                    "year": m.get("Year", "Unknown"),
                                    "type": m.get("Type", "Unknown"),
                                })
                        return out
                        
                except ValueError:
                    continue
                    
            except Exception:
                continue
    
    return []

async def fetch_google_places_cafes(vibe: str, latitude: float, longitude: float, limit: int = 5):
    if not GOOGLE_MAPS_API_KEY:
        return []
    
    search_strategies = [
        {"keyword": f"{vibe} cafe", "type": "cafe"},
        {"keyword": "cafe", "type": "cafe"},
        {"keyword": "coffee", "type": "cafe"},
        {"keyword": "restaurant", "type": "restaurant"},
    ]
    
    for strategy in search_strategies:
        keyword = quote_plus(strategy["keyword"])
        place_type = strategy["type"]
        
        url = (
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            f"?location={latitude},{longitude}&radius=5000&keyword={keyword}&type={place_type}&key={GOOGLE_MAPS_API_KEY}"
        )
        
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, timeout=20)
                r.raise_for_status()
                
                try:
                    data = r.json()
                    if data is None:
                        continue
                    
                    if data.get("status") == "OK":
                        results = data.get("results", [])[:limit]
                        if results:
                            out = []
                            for c in results:
                                if c:
                                    place_id = c.get('place_id', '')
                                    name = c.get("name", "Unknown Cafe")
                                    address = c.get("vicinity", c.get("formatted_address", "Unknown Address"))
                                    rating = c.get("rating", "No rating")
                                    
                                    out.append({
                                        "name": name,
                                        "address": address,
                                        "rating": rating,
                                        "maps_link": f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else "",
                                    })
                            return out
                        
                except ValueError:
                    continue
                    
            except Exception:
                continue
    
    return []

async def geocode_location(location: str) -> tuple[float, float] | None:
    if not GOOGLE_MAPS_API_KEY:
        return None
    
    location_encoded = quote_plus(location)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={location_encoded}&key={GOOGLE_MAPS_API_KEY}"
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, timeout=20)
            r.raise_for_status()
            
            try:
                data = r.json()
                if data is None:
                    return None
                
                if data.get("status") != "OK":
                    return None
                
                results = data.get("results", [])
                if not results:
                    return None
                
                location_data = results[0].get("geometry", {}).get("location", {})
                lat = location_data.get("lat")
                lng = location_data.get("lng")
                
                if lat is not None and lng is not None:
                    return (lat, lng)
                else:
                    return None
                    
            except ValueError:
                return None
                
        except Exception:
            return None

VibePlannerDescription = {
    "description": "Suggest playlists, recipe videos, books, movies and nearby cafes based on a mood/vibe. Provide latitude/longitude for location-aware cafe suggestions.",
    "use_when": "Use when the user wants a contextual, mood-based plan (music, food, reading, movies, cafes).",
    "side_effects": "External API calls are made to Spotify, YouTube, Google Books, OMDb, and Google Places."
}

@mcp.tool(description=VibePlannerDescription["description"])
async def vibe_planner(
    vibe_description: Annotated[str, Field(description="A short mood or vibe, e.g., 'cozy rainy day'")],
    location: Annotated[str | None, Field(description="Location name for nearby cafe search (e.g., 'New York', 'Mumbai', 'Tokyo')")] = None,
    latitude: Annotated[float | None, Field(description="Latitude for nearby cafe search (optional, overrides location)")] = None,
    longitude: Annotated[float | None, Field(description="Longitude for nearby cafe search (optional, overrides location)")] = None,
) -> dict:
    """
    Returns playlists, recipe videos, books, movies, and cafes for a given vibe.
    Supports both location names (e.g., 'Mumbai') and exact coordinates.
    """
    print(f"vibe_planner() called with: {vibe_description}, {location}")
    
    if not vibe_description or not vibe_description.strip():
        raise McpError(ErrorData(code=INVALID_PARAMS, message="vibe_description is required and must not be empty."))

    try:
        # Initialize results with empty lists
        spotify_res = []
        youtube_res = []
        books_res = []
        movies_res = []
        cafes_res = []
        location_info = {}
        
        # Try each API call individually
        try:
            spotify_res = await fetch_spotify_playlists(vibe_description)
        except Exception as e:
            print(f"Spotify failed: {e}")
        
        try:
            youtube_res = await fetch_youtube_recipes(vibe_description)
        except Exception as e:
            print(f"YouTube failed: {e}")
        
        try:
            books_res = await fetch_google_books(vibe_description)
        except Exception as e:
            print(f"Google Books failed: {e}")
        
        try:
            movies_res = await fetch_omdb_movies(vibe_description)
        except Exception as e:
            print(f"OMDb failed: {e}")
        
        # Handle location and cafes
        final_lat, final_lng = latitude, longitude
        
        if (latitude is None or longitude is None) and location:
            try:
                coords = await geocode_location(location)
                if coords:
                    final_lat, final_lng = coords
                    location_info = {
                        "provided_location": location,
                        "geocoded_coordinates": {"latitude": final_lat, "longitude": final_lng},
                        "source": "geocoded"
                    }
            except Exception as e:
                print(f"Geocoding failed: {e}")
        
        if final_lat is not None and final_lng is not None:
            try:
                cafes_res = await fetch_google_places_cafes(vibe_description, final_lat, final_lng)
            except Exception as e:
                print(f"Google Places failed: {e}")

        result = {
            "vibe": vibe_description,
            "spotify_playlists": spotify_res,
            "youtube_recipes": youtube_res,
            "books": books_res,
            "movies": movies_res,
            "cafes": cafes_res,
            "location_info": location_info,
            "debug_info": {
                "spotify_count": len(spotify_res),
                "youtube_count": len(youtube_res),
                "books_count": len(books_res),
                "movies_count": len(movies_res),
                "cafes_count": len(cafes_res)
            }
        }
        
        print(f"vibe_planner() returning: {len(spotify_res)} playlists, {len(cafes_res)} cafes")
        return result

    except McpError:
        raise
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Unexpected error in vibe_planner: {e}"))

# ===== run server =====
async def main():
    # Use Railway's PORT if available, otherwise default to 8086
    port = int(os.environ.get("PORT", 8086))
    print(f"🚀 Starting Vibe Planner MCP server on http://0.0.0.0:{port}")
    print(f"Environment check - AUTH_TOKEN: {'✓' if TOKEN else '❌'}, MY_NUMBER: {'✓' if MY_NUMBER else '❌'}")
    
    await mcp.run_async("streamable-http", host="0.0.0.0", port=port)

if __name__ == "__main__":
    asyncio.run(main())
