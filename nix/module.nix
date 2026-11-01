{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.classroom-qa;
  # Directories the app writes files to for other local users to read
  stateDirs =
    map (lib.removePrefix "/var/lib/")
    (lib.filter (d: d != null) [cfg.archiveDir cfg.sheetsDir]);
  inherit (lib) mkEnableOption mkOption mkIf types;
in {
  options.services.classroom-qa = {
    enable = mkEnableOption "In-Class Q&A + Polling Tool";

    package = mkOption {
      type = types.package;
      default = pkgs.callPackage ./package.nix {};
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix {}";
      description = "The classroom-qa package to use";
    };

    host = mkOption {
      type = types.str;
      default = "127.0.0.1";
      description = "Host address to bind to (use 127.0.0.1 with nginx proxy)";
    };

    port = mkOption {
      type = types.port;
      default = 8000;
      description = "Port to run the FastAPI application on";
    };

    rootPath = mkOption {
      type = types.str;
      default = "";
      example = "/qa";
      description = "Root path when behind a reverse proxy (e.g., /qa for sierra.ucsd.edu/qa)";
    };

    coursesFile = mkOption {
      type = types.path;
      description = "Path to courses.toml configuration file";
    };

    secretKeyFile = mkOption {
      type = types.path;
      default = "/var/secrets/classroom-qa/secret-key";
      description = "Path to file containing HMAC secret key for cookie signing";
    };

    redisPort = mkOption {
      type = types.port;
      default = 6379;
      description = "Port for the bundled Redis instance";
    };

    user = mkOption {
      type = types.str;
      default = "classroom-qa";
      description = "User account under which the service runs";
    };

    group = mkOption {
      type = types.str;
      default = "classroom-qa";
      description = "Group under which the service runs";
    };

    stateDir = mkOption {
      type = types.path;
      default = "/var/lib/classroom-qa";
      description = "Directory for Redis data and runtime state";
    };

    # Rate limiting and session settings
    rateLimitAsk = mkOption {
      type = types.int;
      default = 1;
      description = "Number of questions allowed per rate limit window";
    };

    rateLimitWindow = mkOption {
      type = types.int;
      default = 10;
      description = "Rate limit window in seconds";
    };

    maxQuestionLength = mkOption {
      type = types.int;
      default = 1000;
      description = "Maximum length of student questions";
    };

    sessionTTL = mkOption {
      type = types.int;
      default = 172800;
      description = "Session archive TTL in seconds (48 hours)";
    };

    # Session archives on disk
    archiveDir = mkOption {
      type = types.nullOr types.str;
      default = "/var/lib/classroom-qa/archives";
      description = ''
        Directory to which every archived session is also written, as
        <archiveDir>/<course>/<session-id>.json (same JSON as the Redis
        archive, which still expires after sessionTTL). Files are kept
        forever. Set to null to disable.

        Must be under /var/lib: it is managed as a systemd StateDirectory
        owned by `user`:`group` with mode 0750, and the files are written
        with mode 0640. Members of `group` (see archiveReaders) can read them.
      '';
    };

    archiveReaders = mkOption {
      type = types.listOf types.str;
      default = [];
      example = ["gha-runner"];
      description = ''
        Users to add to `group` so that they can read (but not write) the
        files the service writes for other tools: the session archives in
        archiveDir and the uploaded attendance sheets in sheetsDir. A running
        service of such a user must be restarted to pick up the new group.
      '';
    };

    # Uploaded attendance sheets
    sheetsDir = mkOption {
      type = types.nullOr types.str;
      default = "/var/lib/classroom-qa/sheets";
      description = ''
        Directory for photos/PDFs of paper attendance sheets uploaded from
        the admin "Upload Attendance Sheets" page, stored as
        <sheetsDir>/<course>/<UTC timestamp>-<id>/ with page files and a
        manifest.json written last. Kept forever. Set to null to disable
        uploads.

        Must be under /var/lib: like archiveDir, it is a systemd
        StateDirectory owned by `user`:`group` with mode 0750, and files are
        written with mode 0640, so archiveReaders can read them.
      '';
    };

    sheetsMaxFileBytes = mkOption {
      type = types.int;
      default = 20 * 1024 * 1024;
      description = "Largest single file (photo or PDF) in a sheet upload, in bytes";
    };

    sheetsMaxUploadBytes = mkOption {
      type = types.int;
      default = 80 * 1024 * 1024;
      description = ''
        Largest total size of one sheet upload, in bytes. A reverse proxy in
        front of the app must allow request bodies at least this large on
        the upload URL (nginx's client_max_body_size defaults to 1 MB).
      '';
    };

    sheetsMaxFiles = mkOption {
      type = types.int;
      default = 20;
      description = "Most files (pages or PDFs) in one sheet upload";
    };

    # "Remember this device" on the admin login
    adminRememberDays = mkOption {
      type = types.int;
      default = 365;
      description = ''
        How long, in days, a browser whose admin login had "Remember this
        device" checked stays logged in as that course's admin. All of a
        course's remembered devices can be logged out from its admin page.
      '';
    };

    # Auto-stop of idle sessions
    idleSessionTimeout = mkOption {
      type = types.int;
      default = 7200;
      description = ''
        Automatically stop (and archive) a live session after this many
        seconds without activity (session start, question start/stop, student
        answer, or student question). 0 disables auto-stop.
      '';
    };

    idleCheckInterval = mkOption {
      type = types.int;
      default = 300;
      description = "How often, in seconds, to check for idle sessions";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.archiveDir == null || lib.hasPrefix "/var/lib/" cfg.archiveDir;
        message = "services.classroom-qa.archiveDir must be under /var/lib/ (or null)";
      }
      {
        assertion = cfg.sheetsDir == null || lib.hasPrefix "/var/lib/" cfg.sheetsDir;
        message = "services.classroom-qa.sheetsDir must be under /var/lib/ (or null)";
      }
    ];

    # Create user and group
    users.users.${cfg.user} = {
      isSystemUser = true;
      group = cfg.group;
      description = "Classroom QA service user";
    };

    # archiveReaders join the service group to read the archives and sheets
    users.groups.${cfg.group}.members = cfg.archiveReaders;

    # Bundled Redis service
    systemd.services.classroom-qa-redis = {
      description = "Redis for Classroom QA";
      wantedBy = ["multi-user.target"];
      after = ["network.target"];

      serviceConfig = {
        Type = "simple";
        ExecStart = "${pkgs.redis}/bin/redis-server --port ${toString cfg.redisPort} --dir ${cfg.stateDir}/redis --save \"\" --appendonly no";
        User = cfg.user;
        Group = cfg.group;
        StateDirectory = "classroom-qa/redis";
        Restart = "always";
        RestartSec = "5s";

        # Security hardening
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadWritePaths = cfg.stateDir;
      };
    };

    # Main application service
    systemd.services.classroom-qa = {
      description = "Classroom QA FastAPI Application";
      wantedBy = ["multi-user.target"];
      after = ["network.target" "classroom-qa-redis.service"];
      requires = ["classroom-qa-redis.service"];

      environment = {
        REDIS_URL = "redis://localhost:${toString cfg.redisPort}";
        COURSES_FILE = toString cfg.coursesFile;
        RATE_LIMIT_ASK = toString cfg.rateLimitAsk;
        RATE_LIMIT_WINDOW = toString cfg.rateLimitWindow;
        MAX_QUESTION_LENGTH = toString cfg.maxQuestionLength;
        SESSION_TTL = toString cfg.sessionTTL;
        IDLE_SESSION_TIMEOUT = toString cfg.idleSessionTimeout;
        IDLE_CHECK_INTERVAL = toString cfg.idleCheckInterval;
        SHEETS_MAX_FILE_BYTES = toString cfg.sheetsMaxFileBytes;
        SHEETS_MAX_UPLOAD_BYTES = toString cfg.sheetsMaxUploadBytes;
        SHEETS_MAX_FILES = toString cfg.sheetsMaxFiles;
        ADMIN_REMEMBER_DAYS = toString cfg.adminRememberDays;
      } // lib.optionalAttrs (cfg.archiveDir != null) {
        ARCHIVE_DIR = cfg.archiveDir;
      } // lib.optionalAttrs (cfg.sheetsDir != null) {
        SHEETS_DIR = cfg.sheetsDir;
      };

      serviceConfig = {
        Type = "exec";
        User = cfg.user;
        Group = cfg.group;
        Restart = "always";
        RestartSec = "5s";

        # Load secret key from file
        LoadCredential = "secret-key:${cfg.secretKeyFile}";

        # Files the service creates (session archives, sheet uploads) are
        # readable by its group but not by other users
        UMask = "0027";

        # Security hardening
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadOnlyPaths = cfg.coursesFile;
      } // lib.optionalAttrs (stateDirs != []) {
        # Created (and made writable despite ProtectSystem) by systemd before
        # the service starts, owned by user:group
        StateDirectory = stateDirs;
        StateDirectoryMode = "0750";
      };

      # Set SECRET_KEY from credential file and run uvicorn
      script = ''
        export SECRET_KEY=$(cat ${"\${CREDENTIALS_DIRECTORY}"}/secret-key)
        exec ${cfg.package}/bin/classroom-qa-server \
          --host ${cfg.host} \
          --port ${toString cfg.port} \
          --timeout-graceful-shutdown 5 \
          ${lib.optionalString (cfg.rootPath != "") "--root-path ${cfg.rootPath}"}
      '';
    };

    # No firewall changes needed - nginx already handles port 80/443
  };
}
