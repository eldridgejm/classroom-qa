{
  lib,
  python311,
  fetchFromGitHub,
}:
python311.pkgs.buildPythonApplication {
  pname = "classroom-qa";
  version = "0.1.0";

  src = lib.cleanSource ../.;

  pyproject = true;

  build-system = with python311.pkgs; [
    setuptools
  ];

  dependencies = with python311.pkgs; [
    fastapi
    uvicorn
    redis
    pydantic
    pydantic-settings
    jinja2
    python-multipart
    itsdangerous
    httpx
  ];

  # Don't run tests during build (they require Redis)
  doCheck = false;

  meta = {
    description = "In-Class Q&A + Polling Tool for UCSD Data Science Lectures";
    homepage = "https://github.com/yourusername/classroom-qa";
    license = lib.licenses.mit;
    maintainers = [];
  };
}
