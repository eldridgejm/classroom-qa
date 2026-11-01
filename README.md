# Classroom Q&A

In-Class Q&A + Polling Tool for UCSD Data Science Lectures

A real-time question and answer system built with FastAPI and Redis that allows students to ask questions during lectures and instructors to conduct live polls.

## Features

- Instructors can ask multiple choice, true/false, and short answer questions
- Students can submit answers and change them until the question is closed
- Instructions can see the distribution of student answers in real-time and can optionally share with students
- Students can submit freeform questions
- Instructors can download session data as JSON
- Instructors can photograph paper attendance sheets on a phone and upload them
- Admins can choose to stay logged in on a device ("Remember this device")

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
- `responses`: Object mapping student TSNs to their responses

Each response contains:
- `timestamp`: ISO 8601 timestamp when the response was submitted
- `response`: The student's answer (string for MCQ, boolean for TF, number for numeric)

Sessions are automatically archived when stopped and are retained based on the configured TTL (default: 48 hours). Instructors can download archived sessions as JSON files from the admin interface.

### Archives on Disk

If `ARCHIVE_DIR` is set, every archived session is also written to
`$ARCHIVE_DIR/<course>/<session_id>.json`, with exactly the same JSON as the
Redis archive. These files are permanent (the Redis copy still expires after
`SESSION_TTL`), so other tools (e.g. a CI job recording attendance) can pick
them up. Each file is written to a temporary file in the same directory
(`.<session_id>.*.tmp`) and renamed into place, so a reader that looks only at
`*.json` never sees a partial file. Files are created with mode `0640` and
directories with `0750`, i.e. readable by the service's group. If writing the
file fails, the error is logged and the session is still stopped and archived
in Redis. When `ARCHIVE_DIR` is unset (the default outside NixOS), nothing is
written to disk.

### Idle Sessions

A live session with no activity for `IDLE_SESSION_TIMEOUT` seconds (default: 2
hours) is stopped automatically, exactly as if the instructor had clicked "Stop
Session": it is archived (to Redis and, if configured, to disk) and open
admin/student pages are told the session ended. Activity means the session
starting, a question being started or stopped, a student answer, or a student
question; the time of the last activity is kept in Redis at
`course:<course>:last_activity`. A background task checks every
`IDLE_CHECK_INTERVAL` seconds (default: 5 minutes), so a session is stopped
between 2 hours and 2 hours 5 minutes after it went quiet. Stops (manual or
automatic) hold a per-course Redis lock, so a session is archived only once
even if several app processes check at the same time. Set
`IDLE_SESSION_TIMEOUT=0` to disable auto-stop.

## Attendance Sheet Uploads

If `SHEETS_DIR` is set, each course's admin dashboard links to an "Upload
Attendance Sheets" page (`/<course>/admin/sheets`) made for a phone: it shows a
live viewfinder from the rear camera with a shutter button, so several pages can
be photographed in a row; each shot goes into a strip of thumbnails (tap to
enlarge, × to delete), and Upload sends them all. Photos are taken at the
highest resolution the camera offers, then scaled in the browser to at most
2400 px on the long edge and encoded as JPEG (quality 0.85). Existing photos or
PDFs can be added with "Choose photos or PDFs". The live camera needs a secure
context (HTTPS, or `localhost`); without one, or if the camera can't be used,
the page offers a "Take photo" button that opens the phone's camera app for one
photo at a time instead. An optional lecture number (0-99) can be given; blank
means "the next lecture". Opening the page while logged out shows the admin
login, which returns to the page.

Each upload is stored as its own directory:

```
$SHEETS_DIR/<course>/<UTC timestamp>-<id>/    e.g. 20261103T183012Z-1a2b3c4d
├── page-01.jpg          # images in upload order: page-NN.jpg / .png / .heic
├── page-02.jpg
├── scan-03.pdf          # PDFs are stored as uploaded, numbered in the same sequence
└── manifest.json        # written last
```

```json
{
  "id": "20261103T183012Z-1a2b3c4d",
  "uploaded_at": "2026-11-03T18:30:12Z",
  "course": "dsc190",
  "lecture": 7,
  "files": [
    {"name": "page-01.jpg", "type": "image/jpeg", "bytes": 612376,
     "sha256": "f802…", "original_name": "page-01.jpg"}
  ]
}
```

`lecture` is an integer, or `null` for "the next lecture". Every file is written
to a temporary file (`.<name>.*.tmp`) and renamed into place, and
`manifest.json` is written after all the other files, so **a reader must treat
a directory without `manifest.json` as incomplete** and skip it. If saving
fails, the partial directory is removed. Files are `0640` and directories
`0750`, as for archives. File types are checked by content (JPEG, PNG, HEIC,
PDF), not by name. Limits: `SHEETS_MAX_FILE_BYTES` per file,
`SHEETS_MAX_UPLOAD_BYTES` per upload, `SHEETS_MAX_FILES` files per upload. A
reverse proxy must allow request bodies that large on the upload URL (see
below). Uploads are never deleted by the app.

## Remembered Admin Devices

The admin login has a "Remember this device" checkbox. Without it, login works
as before (a 24-hour `admin_session` cookie). With it, the browser also gets a
cookie `admin_remember_<course>` that keeps it logged in as that course's admin
for `ADMIN_REMEMBER_DAYS` days (default 365) without the password. The cookie
is `HttpOnly`, `SameSite=Lax`, scoped to the app's root path, and holds a
token signed with `SECRET_KEY` that names the course, a per-course version
number, and a fingerprint (not a copy) of the course's admin secret. It is
accepted only for that course, until it expires, while its version is current,
and while the course's secret is unchanged.

"Forget all remembered devices" on the admin page logs out every remembered
device of that course (including the current one) by incrementing the version,
stored in Redis at `admin:course:<course>:remember_version` (outside the
`course:<course>:*` keys that are cleared when a session stops). Password
logins are not affected. Changing the course's `secret` in `courses.toml` also
revokes them; rotating `SECRET_KEY` revokes everything.

The cookie is marked `Secure` when the browser is using HTTPS. The app decides
this from the request's scheme (which uvicorn takes from `X-Forwarded-Proto`
sent by a trusted proxy) or, failing that, from the browser's own `Origin`
header on the login POST (or `Referer` if there is no `Origin`). The latter
matters when HTTPS is terminated in front of the reverse proxy, which then
talks plain HTTP and reports `X-Forwarded-Proto: http`. Those headers come from
the client, but a wrong value only affects the flags of the client's own
cookie.

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
SESSION_TTL=172800        # TTL in seconds (48 hours)
IDLE_SESSION_TIMEOUT=7200 # Auto-stop sessions idle this long (0 = never)
IDLE_CHECK_INTERVAL=300   # How often to check for idle sessions

# Also write archives to <ARCHIVE_DIR>/<course>/<session_id>.json (default: unset)
ARCHIVE_DIR=/path/to/archives

# Enable attendance sheet uploads, stored under <SHEETS_DIR>/<course>/ (default: unset)
SHEETS_DIR=/path/to/sheets

# How long "Remember this device" keeps an admin logged in (days)
ADMIN_REMEMBER_DAYS=365

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
| `SESSION_TTL` | `172800` | Session TTL after end (seconds) |
| `ARCHIVE_DIR` | unset | Also write archives to `<dir>/<course>/<session_id>.json` (unset = disabled) |
| `SHEETS_DIR` | unset | Store attendance sheet uploads in `<dir>/<course>/<upload>/` (unset = uploads disabled) |
| `SHEETS_MAX_FILE_BYTES` | `20971520` | Largest single file in a sheet upload (20 MB) |
| `SHEETS_MAX_UPLOAD_BYTES` | `83886080` | Largest total size of one sheet upload (80 MB) |
| `SHEETS_MAX_FILES` | `20` | Most files in one sheet upload |
| `ADMIN_REMEMBER_DAYS` | `365` | Lifetime of a "Remember this device" login (days) |
| `IDLE_SESSION_TIMEOUT` | `7200` | Auto-stop a session after this many idle seconds (`0` = never) |
| `IDLE_CHECK_INTERVAL` | `300` | How often to check for idle sessions (seconds) |
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
            sessionTTL = 172800;
            idleSessionTimeout = 7200;

            # Archives on disk (default shown; null disables), and users
            # who may read them
            archiveDir = "/var/lib/classroom-qa/archives";
            archiveReaders = [ "gha-runner" ];

            # Attendance sheet uploads (default shown; null disables), also
            # readable by archiveReaders
            sheetsDir = "/var/lib/classroom-qa/sheets";
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

# Sheet uploads are larger than nginx's default 1 MB body limit. Allow at
# least SHEETS_MAX_UPLOAD_BYTES on the upload URL only.
location ~ ^/qa/[^/]+/admin/sheets$ {
    client_max_body_size 100m;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
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
| `sessionTTL` | int | `172800` | Session TTL (seconds) |
| `archiveDir` | null or string | `/var/lib/classroom-qa/archives` | Where archives are also written (must be under `/var/lib`; `null` disables) |
| `archiveReaders` | list of strings | `[]` | Users added to `group` so they can read the archives and sheet uploads |
| `sheetsDir` | null or string | `/var/lib/classroom-qa/sheets` | Where attendance sheet uploads are stored (must be under `/var/lib`; `null` disables uploads) |
| `sheetsMaxFileBytes` | int | `20971520` | Largest file in a sheet upload |
| `sheetsMaxUploadBytes` | int | `83886080` | Largest sheet upload |
| `sheetsMaxFiles` | int | `20` | Most files in a sheet upload |
| `adminRememberDays` | int | `365` | Lifetime of a "Remember this device" login (days) |
| `idleSessionTimeout` | int | `7200` | Auto-stop idle sessions after this many seconds (`0` = never) |
| `idleCheckInterval` | int | `300` | Idle check interval (seconds) |

### Reading Archives and Sheet Uploads as Another User

`archiveDir` and `sheetsDir` are systemd `StateDirectory`s of the service, owned
by `user`:`group` with mode `0750`; the service runs with `UMask=0027`, and the
files are `0640`. So any member of `group` can read the archives and uploads,
and nobody else can write them. To let another local user (say, a GitHub Actions runner
running as `gha-runner`) read them, either set

```nix
services.classroom-qa.archiveReaders = [ "gha-runner" ];
```

or, equivalently, `users.users.gha-runner.extraGroups = [ "classroom-qa" ];`.
Group membership is read when a process starts, so restart that user's
services (e.g. the runner) after the change.

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
