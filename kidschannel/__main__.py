"""Linha de comando do canal automático.

Exemplos:
  python -m kidschannel auth --client-secrets client_secret.json
  python -m kidschannel channel-kit
  python -m kidschannel setup-channel
  python -m kidschannel make --series curiosidades
  python -m kidschannel run --count 1
  python -m kidschannel upload output/20260101-120000-curiosidades-polvo
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config, load_dotenv, series_by_id


def _cmd_auth(args, cfg):
    from .youtube import authorize

    secrets = authorize(args.client_secrets)
    print("\nAutorizado! token.json salvo (uso local).")
    print("Para o GitHub Actions, crie estes *secrets* no repositório:\n")
    for key, value in secrets.items():
        print(f"  {key} = {value}")


def _cmd_channel_kit(args, cfg):
    from .channel_kit import build_kit

    out = build_kit(cfg, offline=args.offline, refresh_mascot=args.refresh_mascot)
    print(f"Kit gerado em {out}. Envie avatar.png como foto do canal no YouTube Studio.")


def _cmd_setup_channel(args, cfg):
    from .config import OUTPUT_DIR
    from .state import ChannelState
    from .youtube import YouTubeClient, YouTubeError

    client = YouTubeClient()
    channel = client.channel()
    print(f"Canal conectado: {channel['snippet']['title']} ({channel['id']})")

    banner = Path(args.banner) if args.banner else OUTPUT_DIR / "channel_kit" / "banner.jpg"
    banner_url = None
    if banner.exists():
        banner_url = client.upload_banner(banner)
        print("Banner enviado.")
    else:
        print(f"Banner não encontrado em {banner} (rode `channel-kit` antes). Seguindo sem banner.")
    client.update_branding(cfg, banner_url)
    print("Descrição, palavras-chave, idioma e país atualizados.")

    try:
        client.mark_channel_for_kids()
        print("Canal marcado como 'feito para crianças'.")
    except YouTubeError as exc:
        print(f"Não deu para marcar o público pela API ({exc}). Faça no YouTube Studio > Configurações > Canal.")

    if cfg["publishing"].get("playlists_per_series", True):
        state = ChannelState()
        for series in cfg["series"]:
            if not state.playlist_for(series["id"]):
                pid = client.create_playlist(
                    f"{series['name']} | {cfg['channel']['name']}", series.get("brief", ""),
                    cfg["channel"].get("language", "pt-BR"),
                )
                state.set_playlist(series["id"], pid)
                print(f"Playlist criada: {series['name']}")
        state.save()
    print("\nPronto! Falta só enviar a foto de perfil (avatar.png) e trocar o nome do canal, se quiser, no YouTube Studio.")


def _cmd_make(args, cfg):
    from .pipeline import make_short
    from .state import ChannelState

    series = series_by_id(cfg, args.series) if args.series else None
    result = make_short(cfg, ChannelState(), series=series, topic=args.topic, offline=args.offline)
    print(f"\nVídeo: {result.video}\nTítulo: {result.script.title}")


def _cmd_run(args, cfg):
    from .pipeline import run

    results = run(cfg, count=args.count, dry_run=args.dry_run, offline=args.offline,
                  series_id=args.series, topic=args.topic)
    for r in results:
        print(f"OK: {r.script.title} -> {r.video}")


def _cmd_upload(args, cfg):
    from .pipeline import load_result, upload_short
    from .state import ChannelState
    from .youtube import YouTubeClient

    result = load_result(cfg, Path(args.folder))
    video_id = upload_short(cfg, ChannelState(), result, YouTubeClient())
    print(f"Publicado: https://youtube.com/shorts/{video_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kidschannel", description="Canal infantil automático no YouTube")
    parser.add_argument("--config", help="caminho do config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth", help="autoriza o canal do YouTube (uma vez, no seu computador)")
    p.add_argument("--client-secrets", default="client_secret.json")
    p.set_defaults(func=_cmd_auth)

    p = sub.add_parser("channel-kit", help="gera mascote, foto de perfil e banner do canal")
    p.add_argument("--offline", action="store_true", help="sem APIs (imagens de teste)")
    p.add_argument("--refresh-mascot", action="store_true", help="gera um novo mascote de referência")
    p.set_defaults(func=_cmd_channel_kit)

    p = sub.add_parser("setup-channel", help="aplica banner, descrição, público infantil e playlists no canal")
    p.add_argument("--banner", help="imagem do banner (padrão: output/channel_kit/banner.jpg)")
    p.set_defaults(func=_cmd_setup_channel)

    for name, func, helptext in [
        ("make", _cmd_make, "gera um Short sem publicar"),
        ("run", _cmd_run, "gera e publica Shorts (usado pelo agendamento diário)"),
    ]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--series", help="força uma série (id do config)")
        p.add_argument("--topic", help="força um tema")
        p.add_argument("--offline", action="store_true", help="sem chaves: roteiro fixo, imagens de teste; voz grátis do Edge se houver internet")
        if name == "run":
            p.add_argument("--count", type=int, help="quantos Shorts produzir (padrão: 1, ou um por horário agendado)")
            p.add_argument("--dry-run", action="store_true", help="gera mas não envia ao YouTube")
        p.set_defaults(func=func)

    p = sub.add_parser("upload", help="publica um Short já gerado (fluxo com revisão manual)")
    p.add_argument("folder", help="pasta do vídeo dentro de output/")
    p.set_defaults(func=_cmd_upload)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("googleapiclient", "httpx", "httpx2", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    load_dotenv()
    cfg = load_config(args.config)
    try:
        args.func(args, cfg)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.getLogger("kidschannel").error("%s", exc, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
