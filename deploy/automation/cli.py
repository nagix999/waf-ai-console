"""WAF-owned deployment entry point. Never echo captured commands or secrets."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy.automation.artifacts import load_settings
from deploy.automation.common import DeployError
from deploy.automation.deployment import Deployment


def main(argv=None):
    parser = argparse.ArgumentParser(description="팀 원본 파일을 수정하지 않는 WAF 배포")
    parser.add_argument("command", choices=("check", "deploy", "status", "rollback"))
    parser.add_argument("--env-file", default=str(ROOT / ".env.production"))
    parser.add_argument("--build-mode", choices=("build", "local"), default="build")
    args = parser.parse_args(argv)
    try:
        operation = Deployment(load_settings(args.env_file, ROOT), build_mode=args.build_mode)
        getattr(operation, args.command)()
        return 0
    except DeployError as error:
        print("중단: " + str(error), file=sys.stderr)
        print("원문 출력은 비밀값 보호를 위해 숨겼습니다. docs/Automated_Team_Deployment.md를 확인하세요.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("중단되었습니다. 재실행 전 status로 확인하고 전환 중이었다면 rollback을 실행하세요.", file=sys.stderr)
        return 130
    except Exception:
        # Tracebacks can include environment values, paths and subprocess output.
        print("중단: unexpected_deployment_error (비밀값 보호로 상세 출력 생략)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
