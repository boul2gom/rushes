"""Static checks of the production image (task 9): no docker here, so read the files."""

import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

DOCKER = Path(__file__).resolve().parent.parent / "docker"


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    data = yaml.safe_load((DOCKER / "compose.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (DOCKER / "Dockerfile").read_text(encoding="utf-8")


def stage(dockerfile: str, name: str) -> str:
    """The instructions of one build stage (from its FROM to the next one)."""
    parts = re.split(r"(?m)^FROM ", dockerfile)
    (body,) = (p for p in parts[1:] if re.search(rf"\bAS {name}$", p.splitlines()[0]))
    return body


def instructions(text: str) -> list[str]:
    """Dockerfile instructions with continuation lines joined, comments dropped."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [line.strip() for line in joined.splitlines() if line.strip() and line[0] != "#"]


def test_compose_limits_and_no_published_port(compose: dict[str, Any]) -> None:
    services = compose["services"]
    assert set(services) == {"rushes", "rushes-test"}
    for name, service in services.items():
        assert service["mem_limit"] == "6g", name
        assert service["cpus"] == 4, name
        assert service["cpu_shares"] == 512, name
        assert "ports" not in service, name  # nothing is published
        assert service["init"] is True, name  # tini: signals reach nice/rushes, zombies are reaped
        assert "network_mode" not in service, name
    assert "ports" not in compose


def test_compose_volumes_and_environment(compose: dict[str, Any]) -> None:
    for name, service in compose["services"].items():
        assert service["volumes"] == ["${RUSHES_DATA_DIR}:/data", "../config:/config:ro"], name
        env = service["environment"]
        assert {k: str(v) for k, v in env.items()} == {
            "OMP_NUM_THREADS": "4",
            "RUSHES_DATA_DIR": "/data",
            "RUSHES_CONFIG_DIR": "/config",
        }, name


def test_compose_test_service_is_a_profile_built_on_the_test_target(
    compose: dict[str, Any],
) -> None:
    services = compose["services"]
    assert services["rushes-test"]["profiles"] == ["test"]
    assert services["rushes-test"]["build"]["target"] == "test"
    assert services["rushes"]["build"]["target"] == "runtime"
    assert "profiles" not in services["rushes"]  # the production service always starts
    assert services["rushes"]["image"] != services["rushes-test"]["image"]


def test_compose_build_points_at_the_dockerfile_from_the_repository_root(
    compose: dict[str, Any],
) -> None:
    for service in compose["services"].values():
        build = service["build"]
        assert (DOCKER / build["context"] / build["dockerfile"]).resolve() == (
            DOCKER / "Dockerfile"
        ).resolve()


def test_entrypoint_is_executable_and_runs_the_command_under_nice() -> None:
    script = DOCKER / "entrypoint.sh"
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "set -eu" in text
    assert 'exec nice -n 10 "$@"' in text
    assert stat.S_IMODE(os.stat(script).st_mode) == 0o755


def test_compose_header_builds_both_images_and_documents_the_data_variable() -> None:
    header = (DOCKER / "compose.yaml").read_text(encoding="utf-8").split("\nname:")[0]
    assert "--profile test build --build-arg RUSHES_COMMIT=" in header
    assert "RUSHES_DATA_DIR" in header and "defined" in header


def test_dockerfile_has_the_three_targets(dockerfile: str) -> None:
    for name in ("builder", "runtime", "test"):
        stage(dockerfile, name)


def test_dockerfile_runtime_runs_as_non_root_through_the_entrypoint(dockerfile: str) -> None:
    runtime = instructions(stage(dockerfile, "runtime"))
    assert "USER rushes" in runtime
    assert not any(line == "USER root" for line in runtime)
    base = instructions(stage(dockerfile, "base"))
    assert any("--uid 1000" in line and "rushes" in line for line in base)
    assert "USER root" not in base
    assert 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in runtime
    assert "COPY docker/entrypoint.sh docker/entrypoint.sh" in runtime  # not all of docker/
    assert "COPY docker/ docker/" not in runtime
    assert runtime.index("USER rushes") < runtime.index('ENTRYPOINT ["/app/docker/entrypoint.sh"]')


def test_dockerfile_records_the_commit_in_the_environment(dockerfile: str) -> None:
    runtime = instructions(stage(dockerfile, "runtime"))
    assert any(line.startswith("ARG RUSHES_COMMIT") for line in runtime)
    assert "ENV RUSHES_COMMIT=${RUSHES_COMMIT}" in runtime


def test_dockerfile_pins_every_base_and_never_uses_latest(dockerfile: str) -> None:
    assert not re.search(r"(?i)latest", "\n".join(instructions(dockerfile)))  # comments aside
    assert re.search(r"ARG PY_IMAGE=python:3\.14-slim-trixie@sha256:[0-9a-f]{64}", dockerfile)
    for source in re.findall(r"--from=(\S+)", dockerfile):
        if not source.startswith(("builder", "runtime")):
            assert re.search(r":\S*\d+\.\d+\.\d+$", source), source  # an explicit version tag


def test_dockerfile_production_image_has_no_dev_tools(dockerfile: str) -> None:
    for name in ("builder", "base", "runtime"):
        lines = instructions(stage(dockerfile, name))
        assert not any("pytest" in line for line in lines), name
        assert not any(re.match(r"COPY (--\S+ )*tests/", line) for line in lines), name
        assert all("--no-dev" in line for line in lines if "uv sync" in line), name
    runtime_env = " ".join(instructions(stage(dockerfile, "runtime")))
    assert "uv sync" not in runtime_env


def test_dockerfile_test_target_adds_dev_dependencies_and_tests(dockerfile: str) -> None:
    test = instructions(stage(dockerfile, "test"))
    assert "COPY tests/ tests/" in test
    assert any("--from=builder-test /app /app" in line for line in test)
    assert not any("--from=builder " in line for line in test)  # no production venv underneath
    assert "USER rushes" in test and 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in test
    dev = instructions(stage(dockerfile, "builder-test"))
    assert any(line.endswith("uv sync --frozen") for line in dev)  # dev group included
    assert "USER root" not in test


def test_dockerfile_keeps_the_avx2_build_flags_and_the_pinned_uv(dockerfile: str) -> None:
    assert "-DGGML_AVX2=ON" in dockerfile and "-DGGML_NATIVE=OFF" in dockerfile
    assert "-DGGML_AVX512=OFF" in dockerfile
    assert re.search(r"ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+ ", dockerfile)
    assert "ffmpeg" in stage(dockerfile, "base")  # runtime and test are built on it


def test_dockerfile_commit_arg_comes_after_the_heavy_layers(dockerfile: str) -> None:
    """A new RUSHES_COMMIT must not invalidate the venv copy of either target."""
    assert "RUSHES_COMMIT" not in " ".join(instructions(stage(dockerfile, "base")))
    for name in ("runtime", "test"):
        lines = instructions(stage(dockerfile, name))
        arg = next(i for i, line in enumerate(lines) if line.startswith("ARG RUSHES_COMMIT"))
        heavy = [i for i, line in enumerate(lines) if line.startswith("COPY --from=builder")]
        assert heavy and max(heavy) < arg, name


def test_dockerfile_precompiles_the_project_sources(dockerfile: str) -> None:
    for name in ("builder", "builder-test"):
        lines = instructions(stage(dockerfile, name))
        assert any("compileall" in line and "/app/src" in line for line in lines), name
