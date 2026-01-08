# Classroom Q&A

In-Class Q&A + Polling Tool for UCSD Data Science Lectures

A real-time question and answer system built with FastAPI and Redis that allows students to ask questions during lectures and instructors to conduct live polls.

## Features

- Instructors can ask multiple choice, true/false, and short answer questions
- Students can submit answers and change them until the question is closed
- Instructions can see the distribution of student answers in real-time and can optionally share with students
- Students can submit freeform questions
- Instructors can download session data as JSON

## Session Archive Format

When instructors end a session, the data is archived in the following JSON format:

```json
{
  "session_id": "arch-1704153600-a1b2c3d4",
  "started_at": "2025-01-01T10:00:00Z",
  "stopped_at": "2025-01-01T11:30:00Z",
  "questions": [
    {
      "question_id": "q-1234567890-abcd",
      "type": "mcq",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "started_at": "2025-01-01T10:15:00Z",
      "ended_at": "2025-01-01T10:20:00Z",
      "responses": {
        "A12345678": {
          "timestamp": "2025-01-01T10:16:30Z",
          "response": "Option B"
        },
        "U87654321": {
          "timestamp": "2025-01-01T10:17:15Z",
          "response": "Option A"
        }
      }
    },
    {
      "question_id": "q-1234567891-efgh",
      "type": "tf",
      "started_at": "2025-01-01T10:25:00Z",
      "ended_at": "2025-01-01T10:28:00Z",
      "responses": {
        "A12345678": {
          "timestamp": "2025-01-01T10:26:00Z",
          "response": true
        }
      }
    }
  ]
}
```

### Field Descriptions

- `session_id`: Unique identifier for the archived session (format: `arch-<timestamp>-<uuid>`)
- `started_at`: ISO 8601 timestamp when the first question was started (may be null)
- `stopped_at`: ISO 8601 timestamp when the session was stopped
- `questions`: Array of question objects from the session

Each question object contains:
- `question_id`: Unique identifier for the question
- `type`: Question type (`mcq`, `tf`, or `numeric`)
- `options`: Array of answer options (only present for MCQ questions)
- `started_at`: ISO 8601 timestamp when the question was started
- `ended_at`: ISO 8601 timestamp when the question was stopped (may be null if still active)
- `responses`: Object mapping student PIDs to their responses

Each response contains:
- `timestamp`: ISO 8601 timestamp when the response was submitted
- `response`: The student's answer (string for MCQ, boolean for TF, number for numeric)

Sessions are automatically archived when stopped and are retained based on the configured TTL (default: 24 hours). Instructors can download archived sessions as JSON files from the admin interface.

## Requirements

### Option 1: Using Nix (Recommended for full development environment)
- Nix (with flakes enabled)
- Redis (bundled in development shell)
- Python 3.11+ (provided by Nix)

### Option 2: Using uv (Lightweight dependency management)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) - Fast Python package installer
- Redis (install separately)

## Running Locally (Development)

### Using Nix (Option 1)

### 1. Enter the Nix Development Shell

```bash
nix develop
```

This will:
- Set up Python 3.11 with all required dependencies
- Start Redis server in the background on port 6379
- Display available commands

### 2. Create a `courses.toml` File

Create a `courses.toml` file in the project root with your course configuration:

```toml
[courses.dsc10-wi25]
secret = "your-course-secret-here"
name = "DSC 10 Winter 2025"
created_at = "2025-01-01T00:00:00Z"

[courses.dsc80-wi25]
secret = "another-course-secret"
name = "DSC 80 Winter 2025"
created_at = "2025-01-01T00:00:00Z"
```

Each course needs:
- A unique slug (e.g., `dsc10-wi25`)
- A `secret` for authentication
- A `name` for display
- A `created_at` timestamp

### 3. (Optional) Configure Environment Variables

Create a `.env` file in the project root or export environment variables:

```bash
# Redis connection (default: redis://localhost:6379)
REDIS_URL=redis://localhost:6379

# Secret key for HMAC cookie signing (change in production!)
SECRET_KEY=your-secret-key-here

# Rate limiting
RATE_LIMIT_ASK=1          # Questions allowed per window
RATE_LIMIT_WINDOW=10      # Window in seconds
MAX_QUESTION_LENGTH=1000  # Max question length

# Session management
SESSION_TTL=86400         # TTL in seconds (24 hours)

# Courses file path (default: courses.toml)
COURSES_FILE=courses.toml
```

### 4. Run the Development Server

```bash
uvicorn app.main:app --reload
```

The app will be available at `http://localhost:8000`

- API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

### 5. Run Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov

# Run specific test file
pytest tests/test_auth.py

# Run specific test
pytest tests/test_auth.py::test_specific_function
```

### 6. Linting and Type Checking

```bash
# Lint with ruff
ruff check .

# Format code
ruff format .

# Type check with mypy
mypy app
```

### Using uv (Option 2)

If you prefer not to use Nix, you can use `uv` for Python dependency management.

#### 1. Install uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with pip
pip install uv
```

#### 2. Start Redis

You'll need to start Redis manually:

```bash
# macOS (with Homebrew)
brew install redis
brew services start redis

# Linux (with apt)
sudo apt install redis-server
sudo systemctl start redis

# Or run in foreground
redis-server
```

#### 3. Install Dependencies

```bash
# Install all dependencies including dev dependencies
uv sync --all-extras
```

#### 4. Create a `courses.toml` File

Same as the Nix setup - create a `courses.toml` file in the project root (see Nix instructions above).

#### 5. Run the Development Server

```bash
uv run uvicorn app.main:app --reload
```

The app will be available at `http://localhost:8000`

#### 6. Run Tests

```bash
# Run all tests
uv run pytest

# Run with coverage report
uv run pytest --cov

# Run specific test file
uv run pytest tests/test_auth.py

# Run specific test
uv run pytest tests/test_auth.py::test_specific_function
```

#### 7. Linting and Type Checking

```bash
# Lint with ruff
uv run ruff check .

# Format code
uv run ruff format .

# Type check with mypy
uv run mypy app
```

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `SECRET_KEY` | `dev-secret-key-change-in-production` | HMAC secret for cookies |
| `RATE_LIMIT_ASK` | `1` | Questions allowed per window |
| `RATE_LIMIT_WINDOW` | `10` | Rate limit window (seconds) |
| `MAX_QUESTION_LENGTH` | `1000` | Max student question length |
| `SESSION_TTL` | `86400` | Session TTL after end (seconds) |
| `COURSES_FILE` | `courses.toml` | Path to courses configuration |

### courses.toml Format

```toml
[courses.<course-slug>]
secret = "authentication-secret"
name = "Course Display Name"
created_at = "2025-01-01T00:00:00Z"  # ISO 8601 format
```

## Deployment (NixOS)

The application includes a NixOS module for production deployment with bundled Redis.

### 1. Add to Your NixOS Configuration

```nix
{
  inputs.classroom-qa.url = "github:yourusername/classroom-qa";

  outputs = { self, nixpkgs, classroom-qa }: {
    nixosConfigurations.yourhost = nixpkgs.lib.nixosSystem {
      modules = [
        classroom-qa.nixosModules.default
        {
          services.classroom-qa = {
            enable = true;
            host = "127.0.0.1";  # Use with nginx reverse proxy
            port = 8000;
            rootPath = "/qa";    # Optional, for reverse proxy subpath

            coursesFile = /path/to/courses.toml;
            secretKeyFile = /var/secrets/classroom-qa/secret-key;

            # Optional: adjust settings
            rateLimitAsk = 1;
            rateLimitWindow = 10;
            maxQuestionLength = 1000;
            sessionTTL = 86400;
          };
        }
      ];
    };
  };
}
```

### 2. Create Required Files

**Secret Key File:**
```bash
# Generate a secure secret key
openssl rand -base64 32 > /var/secrets/classroom-qa/secret-key
chmod 600 /var/secrets/classroom-qa/secret-key
```

**Courses File:**
```bash
# Create courses.toml in a persistent location
cat > /etc/classroom-qa/courses.toml <<EOF
[courses.dsc10-wi25]
secret = "change-this-in-production"
name = "DSC 10 Winter 2025"
created_at = "2025-01-01T00:00:00Z"
EOF
```

### 3. Configure Reverse Proxy (Optional)

Example nginx configuration:

```nginx
location /qa {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # SSE support
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 86400s;
}
```

### 4. Deploy

```bash
# Rebuild NixOS configuration
sudo nixos-rebuild switch

# Check service status
systemctl status classroom-qa
systemctl status classroom-qa-redis

# View logs
journalctl -u classroom-qa -f
journalctl -u classroom-qa-redis -f
```

## NixOS Module Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable` | bool | `false` | Enable the service |
| `package` | package | auto | Package to use |
| `host` | string | `"127.0.0.1"` | Bind address |
| `port` | port | `8000` | Application port |
| `rootPath` | string | `""` | Root path for reverse proxy |
| `coursesFile` | path | required | Path to courses.toml |
| `secretKeyFile` | path | `/var/secrets/classroom-qa/secret-key` | Secret key file |
| `redisPort` | port | `6379` | Bundled Redis port |
| `user` | string | `"classroom-qa"` | Service user |
| `group` | string | `"classroom-qa"` | Service group |
| `stateDir` | path | `/var/lib/classroom-qa` | State directory |
| `rateLimitAsk` | int | `1` | Questions per window |
| `rateLimitWindow` | int | `10` | Window in seconds |
| `maxQuestionLength` | int | `1000` | Max question length |
| `sessionTTL` | int | `86400` | Session TTL (seconds) |

## Architecture

- **FastAPI**: Web framework and API
- **Redis**: Session storage and pub/sub for SSE
- **Uvicorn**: ASGI server
- **Pydantic**: Configuration and data validation
- **Server-Sent Events**: Real-time updates to clients

## Project Structure

```
.
├── app/
│   ├── main.py           # FastAPI application
│   ├── config.py         # Configuration management
│   ├── models.py         # Pydantic models
│   ├── auth.py           # Authentication logic
│   ├── redis_client.py   # Redis client wrapper
│   ├── routes/           # API routes
│   │   ├── admin.py
│   │   ├── student.py
│   │   └── sse.py
│   ├── services/         # Business logic
│   ├── templates/        # Jinja2 templates
│   └── static/           # Static files
├── tests/                # Test suite
├── nix/                  # Nix deployment files
│   ├── module.nix        # NixOS module
│   └── package.nix       # Nix package
├── pyproject.toml        # Python project config
├── flake.nix             # Nix flake
└── courses.toml          # Course configuration (create this)
```

## License

MIT
