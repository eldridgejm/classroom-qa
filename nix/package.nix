{
  lib,
  python311,
  makeWrapper,
}:
let
  pythonEnv = python311.withPackages (ps: with ps; [
    fastapi
    uvicorn
    redis
    pydantic
    pydantic-settings
    jinja2
    python-multipart
    itsdangerous
    httpx
  ]);
in
pythonEnv.pkgs.buildPythonApplication {
  pname = "classroom-qa";
  version = "0.1.0";

  src = lib.cleanSource ../.;

  pyproject = true;

  nativeBuildInputs = [ makeWrapper ];

  build-system = with python311.pkgs; [
    setuptools
  ];

  propagatedBuildInputs = [ pythonEnv ];

  # Don't run tests during build (they require Redis)
  doCheck = false;

  # Create a wrapper script for uvicorn
  postInstall = ''
    makeWrapper ${pythonEnv}/bin/uvicorn $out/bin/classroom-qa-server \
      --add-flags "app.main:app" \
      --set PYTHONPATH "$out/${python311.sitePackages}:${pythonEnv}/${python311.sitePackages}"
  '';

  meta = {
    description = "In-Class Q&A + Polling Tool for UCSD Data Science Lectures";
    homepage = "https://github.com/yourusername/classroom-qa";
    license = lib.licenses.mit;
    maintainers = [];
  };
}
