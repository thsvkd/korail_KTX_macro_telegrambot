"""The setup script's isolated pre-deployment test-bot configuration."""

import contextlib
import os
import shutil
import signal
import socket
import stat
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "scripts" / "setup.sh"
DEPLOY = ROOT / "scripts" / "deploy.sh"
RUN = ROOT / "scripts" / "run.sh"
COMMON = ROOT / "scripts" / "_common.sh"
EXAMPLE = ROOT / ".env.example"


def read_env(path: Path) -> dict[str, str]:
    """Read the simple KEY=VALUE lines written by setup.sh."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def run_setup(tmp_path: Path, answers: str) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run without installing dependencies and keep every write in tmp_path."""
    production = tmp_path / "production.env"
    test = tmp_path / "test.env"
    env = {
        **os.environ,
        "ENV_FILE": str(production),
        "TEST_ENV_FILE": str(test),
    }
    result = subprocess.run(
        ["bash", str(SETUP), "--no-deps", "--test"],
        cwd=ROOT,
        env=env,
        input=answers,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, production, test


def test_test_bot_gets_an_isolated_stack(tmp_path):
    # Defaults for port and image, a distinct token, then no fixed accounts.
    result, production, test = run_setup(tmp_path, "\n\nstaging-token\nn\nn\n")

    assert result.returncode == 0, result.stderr
    settings = read_env(test)
    production_settings = read_env(production)

    assert settings["BOTTOKEN"] == "staging-token"
    assert settings["COMPOSE_PROJECT_NAME"] == "korail-bot-test"
    assert settings["APP_CONTAINER_NAME"] == "korail_bot_test"
    assert settings["REDIS_CONTAINER_NAME"] == "korail_redis_test"
    assert settings["FLASK_PORT"] == "8081"
    assert settings["DEV_REDIS_PORT"] == "6380"
    assert settings["DEV_REDIS_CONTAINER_NAME"] == "korail_test_dev_redis"
    assert settings["BOT_RUNTIME_PROFILE"] == "test"
    assert settings["IMAGE_NAME"] == "korailbot:test"
    assert settings["TRIAL_SEARCH_LIMIT"] == "0"
    assert settings["MAX_CONCURRENT_SEARCHES"] == "1"
    assert settings["RESUME_ON_RESTART"] == "false"
    assert settings["ADMIN_MAGIC_STRING"]
    assert settings["REDIS_PASSWORD"] != production_settings["REDIS_PASSWORD"]
    assert settings["SESSION_SECRET"] != production_settings["SESSION_SECRET"]
    assert stat.S_IMODE(test.stat().st_mode) == 0o600


def test_test_bot_refuses_to_copy_the_production_token(tmp_path):
    production = tmp_path / "production.env"
    shutil.copy(EXAMPLE, production)
    text = production.read_text(encoding="utf-8").replace(
        "BOTTOKEN=your_telegram_bot_token_here", "BOTTOKEN=production-token"
    )
    production.write_text(text, encoding="utf-8")

    result, _, test = run_setup(tmp_path, "\n\nproduction-token\nn\nn\n")

    assert result.returncode == 0, result.stderr
    assert read_env(test)["BOTTOKEN"] == ""
    assert "운영 봇과 같은 토큰은 사용할 수 없습니다" in result.stderr


def test_test_secret_rotation_does_not_change_production(tmp_path):
    production = tmp_path / "production.env"
    test = tmp_path / "test.env"
    production.write_text(
        "SESSION_SECRET=production-session\n"
        "ADMIN_PASSWORD=production-admin\n"
        "REDIS_PASSWORD=production-redis\n",
        encoding="utf-8",
    )
    test.write_text(
        "SESSION_SECRET=old-test-session\n"
        "ADMIN_PASSWORD=old-test-admin\n"
        "REDIS_PASSWORD=old-test-redis\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(SETUP), "secrets", "--force", "--test"],
        cwd=ROOT,
        env={
            **os.environ,
            "ENV_FILE": str(production),
            "TEST_ENV_FILE": str(test),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert read_env(production) == {
        "SESSION_SECRET": "production-session",
        "ADMIN_PASSWORD": "production-admin",
        "REDIS_PASSWORD": "production-redis",
    }
    rotated = read_env(test)
    assert rotated["SESSION_SECRET"] != "old-test-session"
    assert rotated["ADMIN_PASSWORD"] != "old-test-admin"
    assert rotated["REDIS_PASSWORD"] != "old-test-redis"


def test_test_stack_cannot_start_with_the_production_token(tmp_path):
    production = tmp_path / "production.env"
    test = tmp_path / "test.env"
    production.write_text("BOTTOKEN=shared-token\n", encoding="utf-8")
    test.write_text(
        "BOTTOKEN=shared-token\nREDIS_PASSWORD=test-password\n",
        encoding="utf-8",
    )

    # deploy must refuse before invoking Compose. A fake docker executable
    # makes that ordering observable without needing a daemon in unit tests.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "docker-was-called"
    docker = fake_bin / "docker"
    docker.write_text(
        f"#!/usr/bin/env bash\ntouch {marker}\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result = subprocess.run(
        ["bash", str(DEPLOY), "--test", "up"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "ENV_FILE": str(production),
            "TEST_ENV_FILE": str(test),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "different BOTTOKEN" in result.stderr
    assert not marker.exists()


def test_test_stack_uses_its_env_file_and_compose_project(tmp_path):
    production = tmp_path / "production.env"
    test = tmp_path / "test.env"
    production.write_text("BOTTOKEN=production-token\n", encoding="utf-8")
    test.write_text(
        "BOTTOKEN=staging-token\n"
        "REDIS_PASSWORD=test-password\n"
        "COMPOSE_PROJECT_NAME=staging-project\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "docker-calls"
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
printf '%s|%s\n' "${COMPOSE_PROJECT_NAME:-}" "$*" >> "$DOCKER_MARKER"
if [[ "$*" == "compose version" ]]; then
    echo "Docker Compose version v2.30.0"
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result = subprocess.run(
        ["bash", str(DEPLOY), "--test", "up"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_MARKER": str(marker),
            "ENV_FILE": str(production),
            "TEST_ENV_FILE": str(test),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = marker.read_text(encoding="utf-8")
    assert "staging-project|compose version" in calls
    assert (
        f"staging-project|compose --env-file {test} -f {ROOT / 'docker-compose.yml'} up -d"
    ) in calls


def test_all_test_deploy_commands_keep_the_test_stack_selected(tmp_path):
    production = tmp_path / "production.env"
    test = tmp_path / "test.env"
    production.write_text("BOTTOKEN=production-token\n", encoding="utf-8")
    test.write_text(
        "BOTTOKEN=staging-token\n"
        "REDIS_PASSWORD=test-password\n"
        "COMPOSE_PROJECT_NAME=staging-project\n"
        "IMAGE_NAME=registry.example/test-bot:audit\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "docker-calls"
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
printf '%s|%s\n' "${COMPOSE_PROJECT_NAME:-}" "$*" >> "$DOCKER_MARKER"
if [[ "$*" == "compose version" ]]; then
    echo "Docker Compose version v2.30.0"
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_MARKER": str(marker),
        "ENV_FILE": str(production),
        "TEST_ENV_FILE": str(test),
    }
    commands = (
        ["build", "--test"],
        ["--test", "down"],
        ["logs", "--test", "--no-follow"],
    )
    for arguments in commands:
        result = subprocess.run(
            ["bash", str(DEPLOY), *arguments],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{arguments}: {result.stderr}"

    push_result = subprocess.run(
        [
            "bash",
            str(DEPLOY),
            "push",
            "--test",
            "registry.example/test-bot:published",
        ],
        cwd=ROOT,
        env=env,
        input="yes\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert push_result.returncode == 0, push_result.stderr

    calls = marker.read_text(encoding="utf-8")
    assert "staging-project|build -t registry.example/test-bot:audit ." in calls
    assert (
        f"staging-project|compose --env-file {test} -f {ROOT / 'docker-compose.yml'} down" in calls
    )
    assert (
        f"staging-project|compose --env-file {test} -f {ROOT / 'docker-compose.yml'} "
        "logs --tail 100"
    ) in calls
    assert "staging-project|push registry.example/test-bot:published" in calls


def test_test_stack_ignores_the_production_local_override(tmp_path):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(COMMON, scripts / "_common.sh")

    base = project / "docker-compose.yml"
    override = project / "docker-compose.override.yml"
    base.write_text("services: {}\n", encoding="utf-8")
    override.write_text("services: {}\n", encoding="utf-8")

    production = project / ".env"
    test = project / ".env.test"
    production.write_text("BOT_RUNTIME_PROFILE=production\n", encoding="utf-8")
    test.write_text(
        "BOT_RUNTIME_PROFILE=test\nCOMPOSE_PROJECT_NAME=staging-project\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "docker-calls"
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_MARKER"
if [[ "$*" == "compose version" ]]; then
    echo "Docker Compose version v2.30.0"
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; compose config; use_test_stack; compose config',
            "override-test",
            str(scripts / "_common.sh"),
        ],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_MARKER": str(marker),
            "ENV_FILE": str(production),
            "TEST_ENV_FILE": str(test),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config_calls = [
        line for line in marker.read_text(encoding="utf-8").splitlines() if line.endswith("config")
    ]
    assert config_calls == [
        f"compose --env-file {production} -f {base} -f {override} config",
        f"compose --env-file {test} -f {base} config",
    ]


def test_redis_only_stack_does_not_require_a_bot_token(tmp_path):
    production = tmp_path / "production.env"
    production.write_text("REDIS_PASSWORD=test-password\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "docker-calls"
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_MARKER"
if [[ "$*" == "compose version" ]]; then
    echo "Docker Compose version v2.30.0"
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    result = subprocess.run(
        ["bash", str(DEPLOY), "up", "redis"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DOCKER_MARKER": str(marker),
            "ENV_FILE": str(production),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        f"compose --env-file {production} -f {ROOT / 'docker-compose.yml'} up -d redis"
        in marker.read_text(encoding="utf-8")
    )


def test_host_test_runtime_cannot_start_with_the_production_token(tmp_path):
    production = tmp_path / "production.env"
    test = tmp_path / "test.env"
    production.write_text("BOTTOKEN=shared-token\n", encoding="utf-8")
    test.write_text("BOTTOKEN=shared-token\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(RUN), "--test"],
        cwd=ROOT,
        env={
            **os.environ,
            "ENV_FILE": str(production),
            "TEST_ENV_FILE": str(test),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "different BOTTOKEN" in result.stderr


def test_runtime_process_discovery_keeps_production_and_test_apart():
    def process(profile: str) -> subprocess.Popen:
        return subprocess.Popen(
            ["bash", "-c", "exec -a korail_bot.app sleep 30"],
            env={**os.environ, "BOT_RUNTIME_PROFILE": profile},
        )

    production = process("production")
    test = process("test")
    try:
        # Give bash time to replace itself with the tagged process - both of
        # them, and waiting on the environment rather than the command line.
        #
        # The command line is no signal at all here: "korail_bot.app" is one
        # of the words bash was started with, so it matches before the exec
        # it is meant to be waiting for. The environment is what the profile
        # is actually read from, and it is the thing that is briefly not
        # there while execve swaps the program.
        for child in (production, test):
            for _ in range(200):
                if b"BOT_RUNTIME_PROFILE=" in Path(f"/proc/{child.pid}/environ").read_bytes():
                    break
                time.sleep(0.01)

        result = subprocess.run(
            [
                "bash",
                "-c",
                """
source "$1"
BOT_RUNTIME_PROFILE=production
_is_bot "$2" && ! _is_bot "$3" || exit 1
BOT_RUNTIME_PROFILE=test
_is_bot "$3" && ! _is_bot "$2"
""",
                "runtime-profile-test",
                str(COMMON),
                str(production.pid),
                str(test.pid),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
    finally:
        for child in (production, test):
            child.terminate()
            child.wait(timeout=5)


def test_test_daemon_auto_starts_its_local_redis(tmp_path):
    project = tmp_path / "project"
    scripts = project / "scripts"
    bin_dir = tmp_path / "bin"
    venv_bin = project / ".venv" / "bin"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    venv_bin.mkdir(parents=True)
    shutil.copy(COMMON, scripts / "_common.sh")
    shutil.copy(RUN, scripts / "run.sh")

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        redis_port = probe.getsockname()[1]

    production = project / ".env"
    test = project / ".env.test"
    production.write_text("BOTTOKEN=production-token\n", encoding="utf-8")
    test.write_text(
        "BOTTOKEN=staging-token\n"
        "REDIS_PASSWORD=test-password\n"
        "REDIS_HOST=redis\n"
        f"DEV_REDIS_PORT={redis_port}\n"
        "DEV_REDIS_CONTAINER_NAME=staging-dev-redis\n",
        encoding="utf-8",
    )

    docker_calls = tmp_path / "docker-calls"
    redis_pid = tmp_path / "redis-server.pid"
    redis_ready = tmp_path / "redis-server.ready"
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_CALLS"
case "${1:-}" in
    ps)
        [[ -f "$REDIS_READY" ]] && printf '%s\n' "$TEST_REDIS_CONTAINER"
        ;;
    rm)
        exit 0
        ;;
    run)
        "$PYTHON_FOR_TEST" -c '
import pathlib
import socket
import sys

server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", int(sys.argv[1])))
server.listen()
pathlib.Path(sys.argv[2]).touch()
while True:
    connection, _ = server.accept()
    connection.close()
' "$TEST_REDIS_PORT" "$REDIS_READY" >/dev/null 2>&1 &
        printf '%s\n' "$!" > "$REDIS_PID"
        while [[ ! -f "$REDIS_READY" ]]; do sleep 0.01; done
        ;;
    exec)
        printf 'PONG\n'
        ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    uv = bin_dir / "uv"
    uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    uv.chmod(0o755)

    pgrep = bin_dir / "pgrep"
    pgrep.write_text("#!/usr/bin/env bash\nprintf '%s\n' \"$OTHER_BOT_PID\"\n", encoding="utf-8")
    pgrep.chmod(0o755)

    waitress = venv_bin / "waitress-serve"
    waitress.write_text(
        """#!/usr/bin/env bash
trap 'exit 0' TERM INT
printf 'Serving on fake test server\n'
while true; do sleep 1; done
""",
        encoding="utf-8",
    )
    waitress.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ENV_FILE": str(production),
        "TEST_ENV_FILE": str(test),
        "DOCKER_CALLS": str(docker_calls),
        "PYTHON_FOR_TEST": shutil.which("python3") or "python3",
        "REDIS_PID": str(redis_pid),
        "REDIS_READY": str(redis_ready),
        "TEST_REDIS_CONTAINER": "staging-dev-redis",
        "TEST_REDIS_PORT": str(redis_port),
    }

    production_bot = subprocess.Popen(
        ["bash", "-c", "exec -a korail_bot.app sleep 30"],
        env={**os.environ, "BOT_RUNTIME_PROFILE": "production"},
    )
    unrelated_test_bot = subprocess.Popen(
        ["bash", "-c", "exec -a korail_bot.app sleep 30"],
        env={**os.environ, "BOT_RUNTIME_PROFILE": "test"},
    )
    env["OTHER_BOT_PID"] = str(production_bot.pid)

    try:
        result = subprocess.run(
            ["bash", str(scripts / "run.sh"), "--daemon", "--test"],
            cwd=project,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )

        assert result.returncode == 0, f"{result.stderr}\n{result.stdout}"
        assert "Starting the isolated local Redis automatically" in result.stdout
        assert "run -d --name staging-dev-redis" in docker_calls.read_text(encoding="utf-8")
        assert (project / ".run" / "korail-bot-test.pid").is_file()
        assert unrelated_test_bot.poll() is None
    finally:
        for pid_file in (project / ".run" / "korail-bot-test.pid", redis_pid):
            if not pid_file.is_file():
                continue
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
        production_bot.terminate()
        production_bot.wait(timeout=5)
        unrelated_test_bot.terminate()
        unrelated_test_bot.wait(timeout=5)


def test_status_uses_the_running_process_environment_across_worktrees(tmp_path):
    project = tmp_path / "other-worktree"
    scripts = project / "scripts"
    bin_dir = tmp_path / "bin"
    venv_bin = project / ".venv" / "bin"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    venv_bin.mkdir(parents=True)
    shutil.copy(COMMON, scripts / "_common.sh")
    shutil.copy(ROOT / "scripts" / "status.sh", scripts / "status.sh")

    redis_server = socket.socket()
    redis_server.bind(("127.0.0.1", 0))
    redis_server.listen()
    redis_server.settimeout(0.1)
    redis_port = redis_server.getsockname()[1]
    stop_server = threading.Event()

    def accept_connections():
        while not stop_server.is_set():
            try:
                connection, _ = redis_server.accept()
            except TimeoutError:
                continue
            except OSError:
                if stop_server.is_set():
                    return
                raise
            connection.close()

    server_thread = threading.Thread(target=accept_connections, daemon=True)
    server_thread.start()

    (project / ".env").write_text(
        "REDIS_HOST=127.0.0.1\n"
        f"REDIS_PORT={redis_port}\n"
        "REDIS_PASSWORD=wrong-worktree-password\n"
        "SESSION_SECRET=wrong-worktree-secret\n",
        encoding="utf-8",
    )

    marker = tmp_path / "runtime-environment"
    docker_marker = tmp_path / "status-docker-calls"
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/usr/bin/env bash
printf '%s|%s|%s\n' "$REDIS_PASSWORD" "$SESSION_SECRET" "$REDIS_PORT" > "$RUNTIME_ENV_MARKER"
printf '  검색 중인 예약 없음\n'
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    pgrep = bin_dir / "pgrep"
    pgrep.write_text("#!/usr/bin/env bash\nprintf '%s\n' \"$BOT_PID\"\n", encoding="utf-8")
    pgrep.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STATUS_DOCKER_MARKER"
case "${1:-}" in
    ps) printf 'korail_dev_redis\n' ;;
    exec) printf 'PONG\n' ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    python = venv_bin / "python"
    python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)

    actual_password = "password-from-running-production"
    actual_secret = "secret-from-running-production"
    production_bot = subprocess.Popen(
        ["bash", "-c", "exec -a korail_bot.app sleep 30"],
        env={
            **os.environ,
            "BOT_RUNTIME_PROFILE": "production",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(redis_port),
            "REDIS_PASSWORD": actual_password,
            "SESSION_SECRET": actual_secret,
            "FLASK_PORT": "49123",
        },
    )

    try:
        result = subprocess.run(
            ["bash", str(scripts / "status.sh")],
            cwd=project,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "BOT_PID": str(production_bot.pid),
                "RUNTIME_ENV_MARKER": str(marker),
                "STATUS_DOCKER_MARKER": str(docker_marker),
            },
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )

        assert result.returncode == 0, f"{result.stderr}\n{result.stdout}"
        assert marker.read_text(encoding="utf-8").strip() == (
            f"{actual_password}|{actual_secret}|{redis_port}"
        )

        redis_result = subprocess.run(
            ["bash", str(scripts / "status.sh"), "redis", "PING"],
            cwd=project,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "BOT_PID": str(production_bot.pid),
                "RUNTIME_ENV_MARKER": str(marker),
                "STATUS_DOCKER_MARKER": str(docker_marker),
            },
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        assert redis_result.returncode == 0, redis_result.stderr
        assert f"redis-cli -a {actual_password}" in docker_marker.read_text(encoding="utf-8")
    finally:
        production_bot.terminate()
        production_bot.wait(timeout=5)
        stop_server.set()
        redis_server.close()
        server_thread.join(timeout=2)
