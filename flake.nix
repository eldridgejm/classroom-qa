{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs = {
    self,
    nixpkgs,
  }: let
    supportedSystems = ["x86_64-linux" "aarch64-darwin" "x86_64-darwin"];
    forAllSystems = f: nixpkgs.lib.genAttrs supportedSystems (system: f system);
  in {
    # Production package
    packages = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      default = pkgs.callPackage ./nix/package.nix {};
    });

    # NixOS module for deployment
    nixosModules.default = import ./nix/module.nix;

    # Development shell
    devShells = forAllSystems (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};

        pythonEnv = pkgs.python311.withPackages (p: [
          # Runtime dependencies
          p.fastapi
          p.uvicorn
          p.redis
          p.pydantic
          p.pydantic-settings
          p.jinja2
          p.python-multipart
          p.itsdangerous
          p.httpx

          # Dev dependencies
          p.pytest
          p.pytest-asyncio
          p.pytest-cov
          p.ruff
          p.mypy
        ]);
      in {
        default = pkgs.mkShell {
          buildInputs = [
            pythonEnv
            pkgs.redis
          ];

          shellHook = ''
            echo "Starting Redis server in background..."
            redis-server --daemonize yes --port 6379 --dir /tmp

            echo ""
            echo "=================================="
            echo "In-Class Q&A + Polling Tool"
            echo "=================================="
            echo ""
            echo "Python: $(python --version)"
            echo "Redis:  $(redis-server --version | head -n1)"
            echo ""
            echo "Available commands:"
            echo "  pytest              - Run tests"
            echo "  pytest --cov        - Run tests with coverage"
            echo "  ruff check .        - Lint code"
            echo "  mypy app            - Type check"
            echo "  uvicorn app.main:app --reload  - Run dev server"
            echo ""
            echo "Redis is running on localhost:6379"
            echo "=================================="
            echo ""
          '';
        };
      }
    );
  };
}
