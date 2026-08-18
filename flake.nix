{
  description = "hike – DDD building blocks for Python";

  inputs = {
    nixpkgs.url     = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
  };

  outputs = inputs@{ flake-parts, nixpkgs, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {

      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];

      perSystem = { system, ... }:
        let
          pkgs = import nixpkgs { inherit system; };
        in {
          devShells.default = pkgs.mkShell {

            # ── Dev tools ──────────────────────────────────────────────────────
            packages = with pkgs; [
              python314      # >=3.14 required by pyproject.toml
              uv             # package management + venv
              just           # task runner (justfile)
              devenv         # service management (devenv up --profile <name>)
              docker         # required by testcontainers for integration tests
              docker-compose
            ];

            shellHook = ''
              uv sync --all-extras >> /dev/null
              echo "hike dev environment"
              echo "  python  : $(python3 --version 2>&1)"
              echo "  uv      : $(uv --version 2>&1)"
              echo "  just    : $(just --version 2>&1)"
              echo "  docker  : $(docker --version 2>&1)"
              echo ""
              echo "Run 'devenv up [process...]' to start backing services:"
              echo "  devenv up                        all services"
              echo "  devenv up postgres               PostgreSQL  postgresql+psycopg://localhost:5432/hike"
              echo "  devenv up redis                  Redis        redis://127.0.0.1:6379"
              echo "  devenv up mongodb                MongoDB      mongodb://127.0.0.1:27017/?directConnection=true"
              exec "$(getent passwd $USER | cut -d: -f7)"
            '';
          };
        };
    };
}
