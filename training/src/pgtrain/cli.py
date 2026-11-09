"""pgtrain: tune and train pi-guard's detection on this computer. See docs/training.md."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from pgtrain import weights

app = typer.Typer(help="Train and tune pi-guard's detection. Pictures stay on this computer.",
                  no_args_is_help=True)  # fmt: skip
faces_app = typer.Typer(help="Faces: is this me, similar photos, thresholds.", no_args_is_help=True)
persons_app = typer.Typer(help="People: teach the Pi's person detector.", no_args_is_help=True)
reports_app = typer.Typer(help="Anonymous statistics from Pis.", no_args_is_help=True)
app.add_typer(faces_app, name="faces")
app.add_typer(persons_app, name="persons")
app.add_typer(reports_app, name="reports")


def home() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return Path(os.environ.get("PGTRAIN_HOME") or base / "pi-guard-train")


def _tool():  # type: ignore[no-untyped-def]
    from pgtrain.faces import FaceTool

    return FaceTool(weights.ensure_faces(home() / "models"))


@app.command()
def models() -> None:
    """Download the models (checksums checked): the Pi's three, and YOLOX for training."""
    for path in weights.ensure_all(home() / "models"):
        typer.echo(path)


@app.command()
def ui(port: int = 8790) -> None:
    """A page on http://127.0.0.1:PORT for photos: who is this, similar photos."""
    from pgtrain.web import serve

    serve(home(), port)


@faces_app.command("compare")
def faces_compare(first: Path, second: Path) -> None:
    """Are the biggest faces of two photos the same person?"""
    from pi_guard.detection.faces import person_scores

    tool = _tool()
    a, b = tool.main_face(first), tool.main_face(second)
    for name, face in ((first, a), (second, b)):
        if face is None or face.embedding is None:
            typer.echo(f"{name}: {face.problem if face else 'no face'}")
            raise typer.Exit(1)
    score = person_scores(a.embedding, [("b", b.embedding)])["b"]  # type: ignore[union-attr,arg-type]
    typer.echo(f"similarity {score:.2f}: the Pi would say {tool.verdict(score)}")


@faces_app.command("whois")
def faces_whois(photo: Path, people: Path = typer.Option(None, help="folder/<name>/*.jpg")) -> None:
    """Whom the Pi would see in each face of a photo."""
    from pgtrain.faces import who

    tool = _tool()
    known = tool.people(people or home() / "people")
    for number, face in enumerate(tool.faces(photo), 1):
        if face.embedding is None:
            typer.echo(f"face {number} {face.box}: cannot be judged ({face.problem})")
            continue
        ranked = who(face.embedding, known)[:3]
        best = ranked[0] if ranked else ("nobody", 0.0)
        typer.echo(f"face {number} {face.box}: {tool.verdict(best[1])} {best[0]} ({best[1]:.2f}); "
                   + ", ".join(f"{n} {s:.2f}" for n, s in ranked[1:]))  # fmt: skip


@faces_app.command("search")
def faces_search(person: Path, photos: Path, top: int = 20) -> None:
    """Photos in PHOTOS that show the person whose photos are in PERSON, best first."""
    from pgtrain.faces import images, who

    tool = _tool()
    known = {person.name: [f.embedding for p in images(person)
                           if (f := tool.main_face(p)) and f.embedding is not None]}  # fmt: skip
    if not known[person.name]:
        raise typer.BadParameter("no usable face in the person's photos")
    hits = []
    for photo in images(photos):
        scores = [
            who(f.embedding, known)[0][1] for f in tool.faces(photo) if f.embedding is not None
        ]
        if scores:
            hits.append((max(scores), photo))
    for score, photo in sorted(hits, reverse=True)[:top]:
        typer.echo(f"{score:.2f}  {tool.verdict(score):9}  {photo}")


@faces_app.command("calibrate")
def faces_calibrate(
    people: Path = typer.Option(None, help="folder/<name>/*.jpg, at least two people"),
    reports: Path = typer.Option(None, help="folder of received statistics"),
    margin: float = 0.06,
) -> None:
    """Where the Pi's threshold should be, from labelled photos and the Pis' corrections."""
    from pi_guard.config.files import FaceSettings
    from pi_guard.diagnostics.contribution import SCORE_BINS

    from pgtrain.faces import from_histogram, leave_one_out, rates, recommend

    genuine: list[float] = []
    impostor: list[float] = []
    if people or (home() / "people").is_dir():
        g, i = leave_one_out(_tool().people(people or home() / "people"))
        genuine, impostor = genuine + g, impostor + i
    if reports:
        from pgtrain.reports import load, summary

        corrections = summary(load(reports))["corrections"]
        genuine += list(from_histogram(corrections["is"], SCORE_BINS))
        impostor += list(from_histogram(corrections["not"], SCORE_BINS))
    current = FaceSettings()
    typer.echo("now:         " + json.dumps(rates(genuine, impostor, current.match_threshold,
                                                  current.uncertain_margin)))  # fmt: skip
    best = recommend(genuine, impostor, margin)
    if best is None:
        typer.echo("Not enough pictures of different people to suggest a threshold.")
        return
    typer.echo("suggested:   " + json.dumps(rates(genuine, impostor, best, margin)))
    typer.echo(f"faces:\n  match_threshold: {best}\n  uncertain_margin: {margin}")


@persons_app.command("train")
def persons_train(
    pictures: Path,
    out: Path = typer.Option(None, help="where checkpoints go"),
    epochs: int = 10,
    batch: int = typer.Option(16, help="pictures per GPU and step"),
    devices: int = typer.Option(2, help="GPUs to use"),
    lr: float = 2e-5,
    limit: int = typer.Option(0, help="use only this many pictures, for a quick try"),
) -> None:
    """Teach YOLOX-Nano from YOLOX-L on a folder of pictures (any size, no labels needed)."""
    from pgtrain.persons import TrainConfig, train

    folder = weights.ensure_training(home() / "models")
    target = out or home() / "runs" / "persons"
    train(TrainConfig(str(pictures), str(target), str(folder["nano"]), str(folder["l"]),
                      epochs=epochs, batch=batch, lr=lr, devices=devices, limit=limit))  # fmt: skip
    typer.echo(f"done: {target / 'best.pth'}; next: pgtrain persons export {target / 'best.pth'}")
    typer.echo(
        "Then compare it with `pgtrain persons evaluate`: use it only if it finds more people."
    )


@persons_app.command("export")
def persons_export(checkpoint: Path, out: Path = Path("yolox_nano.onnx")) -> None:
    """Turn a checkpoint into the ONNX file the Pi loads."""
    from pgtrain.persons import export

    path = export(checkpoint, out)
    typer.echo(f"{path} works with the Pi's code; sha256 {weights.sha256(path)}")


@persons_app.command("evaluate")
def persons_evaluate(
    pictures: Path,
    model: list[Path] = typer.Option(None, help="ONNX files to compare; default: the Pi's"),
    limit: int = 0,
    whole: bool = typer.Option(False, help="check on all of PICTURES, for a folder kept apart"),
) -> None:
    """Compare ONNX models on the checking share of PICTURES, through pi-guard's own code.

    Pictures a camera takes seconds apart look alike. To judge on pictures of your own
    camera, keep later days in a folder of their own and check with --whole."""
    from pgtrain.persons import evaluate

    folder = weights.ensure_training(home() / "models")
    models = model or [weights.ensure_faces(home() / "models") / "yolox_nano.onnx"]
    typer.echo(json.dumps(evaluate(pictures, folder["l"], models, limit, whole), indent=2))


@reports_app.command("receive")
def reports_receive(folder: Path, host: str = "127.0.0.1", port: int = 8780) -> None:
    """Accept reports on POST /v1/report. Put it behind HTTPS, e.g. a reverse proxy."""
    from aiohttp import web

    from pgtrain.reports import receiver

    folder.mkdir(parents=True, exist_ok=True)
    web.run_app(receiver(folder), host=host, port=port, access_log=None)  # no addresses kept


@reports_app.command("summary")
def reports_summary(folder: Path) -> None:
    """Add up all received reports."""
    from pgtrain.reports import load, summary

    typer.echo(json.dumps(summary(load(folder)), indent=2))


def main() -> None:
    app()
