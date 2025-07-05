# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

lx-music-api-server is an API backend implementation for LX Music that provides music streaming services from various Chinese music platforms (Kugou, QQ Music, NetEase Cloud Music, Migu, Kuwo).

This is a fork with additional features:
- Local music source matching mechanism
- Remote audio caching
- External script support for third-party sources
- WebDAV integration for cloud storage

## Development Commands

### Running the Server

```bash
# Direct run with Python
python main.py

# Using Poetry
poetry install
poetry shell
python main.py

# Using Docker
docker-compose up

# Build Docker base image
docker-compose --profile build-base build
```

### Testing

```bash
# Run script generation test
python test/test_script_generation.py

# Test metadata functionality
python test/test_meta.py
```

### Dependencies

```bash
# Install with pip
pip install -r requirements.txt

# Install with Poetry
poetry install
```

## Architecture Overview

### Core Components

1. **Main Server** (`main.py`): 
   - Async HTTP server using aiohttp
   - Handles routing for music API endpoints
   - Manages SSL/HTTPS configuration
   - Implements rate limiting and security features

2. **Module System** (`modules/`):
   - Each music platform has its own module (kg, tx, wy, mg, kw)
   - `modules/__init__.py` handles URL requests, caching, and metadata management
   - `external_script.py` manages third-party JavaScript sources

3. **Configuration** (`common/config.py`):
   - Manages YAML configuration loading
   - SQLite database for caching
   - Redis support for distributed caching
   - IP ban list management

4. **Caching System**:
   - URL caching with expiration times per platform
   - Audio file caching to local disk
   - Metadata (info, lyrics, covers) caching
   - In-memory cache index for fast lookups

5. **Security** (`common/lxsecurity.py`):
   - Request key validation
   - LXM header verification
   - IP-based rate limiting and banning

### API Endpoints

- `/{method}/{source}/{songId}/{quality}` - Main music API endpoint
- `/local/{type}` - Local music file access
- `/cache/{filename}` - Cached audio file access
- `/script` - Download LX Music source script

### Music Sources

Each source module implements:
- `url()` - Get streaming URL
- `lyric()` - Get lyrics
- `search()` - Search songs
- `info()` - Get song metadata
- Platform-specific authentication/refresh

### Special Features

1. **Local Music Matching**: 
   - Searches `audio_path` directory for matching files
   - Returns local file URLs when available

2. **Remote Cache**:
   - Downloads audio to `cache_audio` directory
   - Embeds metadata into cached files (MP3/FLAC)
   - Serves cached files on subsequent requests

3. **External Scripts**:
   - Loads JavaScript sources from configured URLs
   - Falls back to external scripts when internal sources fail
   - Caches scripts locally in `external_scripts/`

4. **WebDAV Support**:
   - Can read from WebDAV servers for cloud storage
   - Supports authentication and SSL verification

## Configuration

Main configuration file: `config/config.yml`

Key sections:
- `common`: Server settings, ports, SSL, caching
- `security`: Rate limiting, key authentication, IP banning
- `module`: Per-platform settings and credentials
- `cookiepool`: Multiple account support per platform

## Important Implementation Details

1. **Async Architecture**: All I/O operations use asyncio for concurrency

2. **Cache Management**: 
   - Uses memory index to avoid disk scanning
   - Implements deduplication for concurrent metadata fetches
   - Supports both SQLite and Redis backends

3. **Error Handling**:
   - `FailedException` for expected failures
   - Automatic fallback to external scripts
   - Retry logic for network requests

4. **Platform Specifics**:
   - Kugou: Song IDs converted to lowercase for consistency
   - Each platform has different token expiration times
   - Cookie/token refresh mechanisms vary by platform

5. **Script Generation**:
   - Template at `lx-music-source.js.template`
   - Dynamic replacement of API URLs and keys
   - Embeds configuration into generated script