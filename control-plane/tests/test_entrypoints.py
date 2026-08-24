from collections.abc import Callable

import pytest

from echo.api.main import cli as api_cli
from echo.planner.main import cli as planner_cli
from echo.scanner.main import cli as scanner_cli


@pytest.mark.parametrize(
    ("entrypoint", "expected"),
    [
        (api_cli, "echo-api"),
        (planner_cli, "echo-planner"),
        (scanner_cli, "echo-scanner"),
    ],
)
def test_entrypoint_identity(
    entrypoint: Callable[[], None], expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    entrypoint()
    assert capsys.readouterr().out.strip() == expected

