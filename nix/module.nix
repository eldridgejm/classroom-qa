{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.classroom-qa;
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

    escalationThreshold = mkOption {
      type = types.int;
      default = 3;
      description = "Number of similar questions to trigger escalation";
    };

    sessionTTL = mkOption {
      type = types.int;
      default = 1800;
      description = "Session data TTL in seconds (30 minutes)";
    };
  };

  config = mkIf cfg.enable {
    # Create user and group
    users.users.${cfg.user} = {
      isSystemUser = true;
      group = cfg.group;
      description = "Classroom QA service user";
    };

    users.groups.${cfg.group} = {};

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
        ESCALATION_THRESHOLD = toString cfg.escalationThreshold;
        SESSION_TTL = toString cfg.sessionTTL;
      };

      serviceConfig = {
        Type = "exec";
        User = cfg.user;
        Group = cfg.group;
        Restart = "always";
        RestartSec = "5s";

        # Load secret key from file
        LoadCredential = "secret-key:${cfg.secretKeyFile}";

        # Security hardening
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadOnlyPaths = cfg.coursesFile;
      };

      # Set SECRET_KEY from credential file and run uvicorn
      script = ''
        export SECRET_KEY=$(cat ${"\${CREDENTIALS_DIRECTORY}"}/secret-key)
        exec ${cfg.package}/bin/classroom-qa-server \
          --host ${cfg.host} \
          --port ${toString cfg.port} \
          ${lib.optionalString (cfg.rootPath != "") "--root-path ${cfg.rootPath}"}
      '';
    };

    # No firewall changes needed - nginx already handles port 80/443
  };
}
