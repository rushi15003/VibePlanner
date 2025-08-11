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

# ===== Auth provider =====
class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key, jwks_uri=None, issuer=None, audience=None)
        self.token = token

    async def load_access_token(self, token: str) -> AccessToken | None:
        if token == self.token:
            return AccessToken(
                token=token,
                client_id="puch-client",
                scopes=["*"],
                expires_at=None,
            )
        return None

# ===== ENABLE STATELESS MODE =====
mcp = FastMCP(
    "Vibe Planner MCP Server",
    auth=SimpleBearerAuthProvider(TOKEN),
    stateless_http=True  # <-- crucial change, no session IDs needed
)

# ===== validate tool =====
@mcp.tool
async def validate() -> str:
    return MY_NUMBER
@mcp.tool
async def about() -> dict:
    return {"name": "VibePlanner", "description": "Let Puch AI plan your day by giving recommendations for music, books, movies, recipes, nearby cafes, etc by just your mood"}
    
# ===== helper: spotify client creds (async) =====
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
            json_data = resp.json()
            if json_data is None:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message="Spotify returned empty response"))
            return json_data.get("access_token")
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
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Spotify fetch error: {e}"))

# ===== helper: youtube recipe search =====
async def fetch_youtube_recipes(vibe: str, limit: int = 5):
    if not YOUTUBE_API_KEY:
        return []
    q = quote_plus(f"{vibe} recipe")
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={q}&type=video&maxResults={limit}&key={YOUTUBE_API_KEY}"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
            out = []
            for it in items:
                if it:
                    vid_id = it.get("id", {}).get("videoId")
                    title = it.get("snippet", {}).get("title")
                    if vid_id and title:
                        out.append({"title": title, "link": f"https://www.youtube.com/watch?v={vid_id}"})
            return out
        except Exception as e:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"YouTube fetch error: {e}"))

# ===== helper: google books =====
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
    
    # Try different search strategies for better results
    search_terms = [
        vibe,  # Original vibe
        f"{vibe} movie",  # Add "movie" to be more specific
        # Try mood-based movie terms if original vibe doesn't work
    ]
    
    # Map vibes to movie genres/themes for better results
    vibe_lower = vibe.lower()
    if "cozy" in vibe_lower or "rainy" in vibe_lower:
        search_terms.append("romantic comedy")
        search_terms.append("drama")
    elif "adventure" in vibe_lower or "exciting" in vibe_lower:
        search_terms.append("action")
        search_terms.append("adventure")
    elif "scary" in vibe_lower or "spooky" in vibe_lower:
        search_terms.append("horror")
    elif "funny" in vibe_lower or "comedy" in vibe_lower:
        search_terms.append("comedy")
    
    for search_term in search_terms:
        q = quote_plus(search_term)
        url = f"https://www.omdbapi.com/?apikey={OMDB_API_KEY}&s={q}"
        print(f"OMDb: Trying search term '{search_term}' -> {url}")
        
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, timeout=20)
                r.raise_for_status()
                
                try:
                    data = r.json()
                    print(f"OMDb response for '{search_term}': {data}")
                    
                    if data is None:
                        continue
                    
                    # OMDb returns "False" for Response when no results found
                    if data.get("Response") == "False":
                        error_msg = data.get("Error", "No results found")
                        print(f"OMDb: No results for '{search_term}' - {error_msg}")
                        continue  # Try next search term
                        
                    results = data.get("Search", [])[:limit]
                    print(f"OMDb: Found {len(results)} results for '{search_term}'")
                    
                    if results:  # If we found results, return them
                        out = []
                        for m in results:
                            if m:  # Check if movie object exists
                                out.append({
                                    "title": m.get("Title", "Unknown Title"),
                                    "year": m.get("Year", "Unknown"),
                                    "type": m.get("Type", "Unknown"),
                                })
                        return out
                        
                except ValueError as json_error:
                    print(f"OMDb JSON error for '{search_term}': {json_error}")
                    continue  # Try next search term
                    
            except httpx.HTTPError as http_error:
                print(f"OMDb HTTP error for '{search_term}': {http_error}")
                continue  # Try next search term
            except Exception as e:
                print(f"OMDb general error for '{search_term}': {e}")
                continue  # Try next search term
    
    print("OMDb: All search terms failed")
    return []  # Return empty if all search terms fail


# ===== helper: Google Places (cafes) =====
async def fetch_google_places_cafes(vibe: str, latitude: float, longitude: float, limit: int = 5):
    if not GOOGLE_MAPS_API_KEY:
        print(f"Google Places: No API key provided")
        return []
    
    # Try multiple search strategies for better results
    search_strategies = [
        # Strategy 1: Vibe-specific cafes
        {"keyword": f"{vibe} cafe", "type": "cafe"},
        # Strategy 2: Just cafes nearby
        {"keyword": "cafe", "type": "cafe"},
        # Strategy 3: Coffee shops
        {"keyword": "coffee", "type": "cafe"},
        # Strategy 4: Restaurants if cafes don't work
        {"keyword": "restaurant", "type": "restaurant"},
    ]
    
    for i, strategy in enumerate(search_strategies):
        keyword = quote_plus(strategy["keyword"])
        place_type = strategy["type"]
        
        # Try with type parameter
        url = (
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            f"?location={latitude},{longitude}&radius=5000&keyword={keyword}&type={place_type}&key={GOOGLE_MAPS_API_KEY}"
        )
        
        print(f"Google Places attempt {i+1}: '{strategy['keyword']}' -> {url}")
        
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, timeout=20)
                r.raise_for_status()
                
                try:
                    data = r.json()
                    print(f"Google Places response {i+1}: status = {data.get('status')}, results count = {len(data.get('results', []))}")
                    
                    if data is None:
                        continue
                    
                    status = data.get("status")
                    
                    # Handle different status codes
                    if status == "OK":
                        results = data.get("results", [])[:limit]
                        if results:  # If we found results, return them
                            out = []
                            for c in results:
                                if c:  # Check if cafe object exists
                                    place_id = c.get('place_id', '')
                                    name = c.get("name", "Unknown Cafe")
                                    address = c.get("vicinity", c.get("formatted_address", "Unknown Address"))
                                    rating = c.get("rating", "No rating")
                                    
                                    out.append({
                                        "name": name,
                                        "address": address,
                                        "rating": rating,
                                        "maps_link": f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else "",
                                        "search_strategy": strategy["keyword"]
                                    })
                            print(f"Google Places: Found {len(out)} places with strategy '{strategy['keyword']}'")
                            return out
                        else:
                            print(f"Google Places: Strategy '{strategy['keyword']}' returned OK but no results")
                    
                    elif status == "ZERO_RESULTS":
                        print(f"Google Places: No results for strategy '{strategy['keyword']}'")
                        continue  # Try next strategy
                    
                    elif status == "REQUEST_DENIED":
                        error_msg = data.get("error_message", "Request denied - check API key")
                        print(f"Google Places ERROR: {error_msg}")
                        return []  # Don't try other strategies if API key is bad
                    
                    elif status == "OVER_QUERY_LIMIT":
                        error_msg = data.get("error_message", "Over query limit")
                        print(f"Google Places ERROR: {error_msg}")
                        return []  # Don't try other strategies if over limit
                    
                    else:
                        error_msg = data.get("error_message", f"Unknown status: {status}")
                        print(f"Google Places: {error_msg}")
                        continue  # Try next strategy
                        
                except ValueError as json_error:
                    print(f"Google Places JSON error for strategy '{strategy['keyword']}': {json_error}")
                    continue  # Try next strategy
                    
            except httpx.HTTPError as http_error:
                print(f"Google Places HTTP error for strategy '{strategy['keyword']}': {http_error}")
                continue  # Try next strategy
            except Exception as e:
                print(f"Google Places general error for strategy '{strategy['keyword']}': {e}")
                continue  # Try next strategy
    
    print("Google Places: All search strategies failed")
    return []

# ===== helper: geocode =====
async def geocode_location(location: str) -> tuple[float, float] | None:
    if not GOOGLE_MAPS_API_KEY:
        return None
    location_encoded = quote_plus(location)
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={location_encoded}&key={GOOGLE_MAPS_API_KEY}"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "OK":
                return None
            loc = data.get("results", [])[0]["geometry"]["location"]
            return (loc["lat"], loc["lng"])
        except:
            return None

VibePlannerDescription = {
    "description": "Suggest playlists, recipe videos, books, movies and nearby cafes based on a mood/vibe.",
    "use_when": "Use for contextual, mood-based plans.",
    "side_effects": "External API calls to Spotify, YouTube, Google Books, OMDb, Google Places."
}

# ===== main planner tool =====
@mcp.tool(description=VibePlannerDescription["description"])
async def vibe_planner(
    vibe_description: Annotated[str, Field(description="Short mood/vibe")],
    location: Annotated[str | None, Field(description="Location name")] = None,
    latitude: Annotated[float | None, Field(description="Latitude")] = None,
    longitude: Annotated[float | None, Field(description="Longitude")] = None,
) -> dict:
    if not vibe_description.strip():
        raise McpError(ErrorData(code=INVALID_PARAMS, message="vibe_description required"))
    spotify_res, youtube_res, books_res, movies_res, cafes_res = [], [], [], [], []
    location_info = {}
    try:
        spotify_res = await fetch_spotify_playlists(vibe_description)
        youtube_res = await fetch_youtube_recipes(vibe_description)
        books_res = await fetch_google_books(vibe_description)
        movies_res = await fetch_omdb_movies(vibe_description)
        final_lat, final_lng = latitude, longitude
        if (latitude is None or longitude is None) and location:
            coords = await geocode_location(location)
            if coords:
                final_lat, final_lng = coords
                location_info = {"provided_location": location, "geocoded_coordinates": {"latitude": final_lat, "longitude": final_lng}}
        if final_lat and final_lng:
            cafes_res = await fetch_google_places_cafes(vibe_description, final_lat, final_lng)
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Unexpected error: {e}"))
    return {
        "vibe": vibe_description,
        "spotify_playlists": spotify_res,
        "youtube_recipes": youtube_res,
        "books": books_res,
        "movies": movies_res,
        "cafes": cafes_res,
        "location_info": location_info
    }

# ===== run server =====
async def main():
    print("🚀 Starting Vibe Planner MCP server in STATELESS mode on http://0.0.0.0:8086")
    await mcp.run_async("streamable-http", host="0.0.0.0", port=8086)

if __name__ == "__main__":
    asyncio.run(main())
