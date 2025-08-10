# 🌟 Vibe Planner MCP Server

This is a stateless MCP server built with **FastMCP** that generates mood-based recommendations for:
- Spotify playlists
- YouTube recipes
- Google Books
- OMDb movies
- Google Maps cafes

It also provides geocoded location details.  
The server uses **Bearer Token authentication** and runs in **stateless HTTP mode**.

---

## Tools

### `vibe_planner`
Generates vibe-based recommendations.

**Parameters:**
- `vibe_description` (string, required) — description of the vibe or mood  
- `location` (string, optional) — location for cafe recommendations

**Returns:**
- `vibe` — original vibe description
- `spotify_playlists` — list of playlists (name, link, image)
- `youtube_recipes` — list of recipes (title, link)
- `books` — list of books (title, authors, link)
- `movies` — list of movies (title, year, type)
- `cafes` — list of cafes (name, address, rating, maps_link, search_strategy)
- `location_info` — provided location and geocoded coordinates

---

# Setup
### 1 Create a virtual environment:
```bash
python -m venv venv
venv\Scripts\activate
```
### 2 Install dependencies:
```bash
pip install -r requirements.txt
```

### 3 Create .env file:

```bash
AUTH_TOKEN=your_auth_token_here
MY_NUMBER=your_number_here
SPOTIFY_CLIENT_ID=your_spotify_client_id_here
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret_here
YOUTUBE_API_KEY=your_youtube_api_key_here
OMDB_API_KEY=your_omdb_api_key_here
GOOGLE_MAPS_API_KEY=your_google_maps_api_key_here
```
## 4 Running the Server
```bash
python server.py
```

# 🧪 Testing
- Postman
```
curl -X POST "http://localhost:8086/tools/vibe_planner" ^
     -H "Authorization: Bearer your_auth_token_here" ^
     -H "Content-Type: application/json" ^
     -d "{\"vibe_description\": \"cozy rainy day\", \"location\": \"Pune, India\"}"
```
