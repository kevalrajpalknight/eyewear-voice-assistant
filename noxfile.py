import nox

nox.options.default_venv_backend = "uv"
nox.options.sessions = ["lint", "typecheck", "tests"]

PYTHONS = ["3.10", "3.11", "3.12", "3.13"]


@nox.session(python="3.12")
def lint(session: nox.Session) -> None:
    session.install("ruff")
    session.run("ruff", "check", ".")
    session.run("ruff", "format", "--check", ".")


@nox.session(python="3.12")
def typecheck(session: nox.Session) -> None:
    session.install("-e", ".", "mypy", "pytest", "pytest-asyncio", "types-pyyaml")
    session.run("mypy")


@nox.session(python=PYTHONS)
def tests(session: nox.Session) -> None:
    session.install("-e", ".", "pytest", "pytest-asyncio", "pytest-cov")
    session.run("pytest", *session.posargs)
