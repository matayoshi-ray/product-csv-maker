import os

from rembg import new_session


def main() -> None:
    model_name = os.environ.get("REMBG_MODEL", "u2netp")
    new_session(model_name)
    print(f"Background removal model ready: {model_name}")


if __name__ == "__main__":
    main()
