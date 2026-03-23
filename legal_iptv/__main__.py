from legal_iptv.cli import parse_args
from legal_iptv.config import AppConfig
from legal_iptv.logging_config import configure_logging
from legal_iptv.services.aggregate import run_aggregation


def main() -> None:
    args = parse_args()
    config = AppConfig.from_args(args)
    configure_logging(config.log_level)
    run_aggregation(config)


if __name__ == "__main__":
    main()