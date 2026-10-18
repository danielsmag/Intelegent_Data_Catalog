"""Exit 0 when the kernel gateway process is answering."""

import urllib.error
import urllib.request


def main() -> None:
    """Treat auth failures as healthy. Connection errors are not."""
    try:
        urllib.request.urlopen("http://127.0.0.1:8888/api")
    except urllib.error.HTTPError as err:
        if err.code not in (200, 401, 403):
            raise SystemExit(1) from err
    except OSError as err:
        raise SystemExit(1) from err


if __name__ == "__main__":
    main()
