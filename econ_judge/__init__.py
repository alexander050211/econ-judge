import os

from flask import Blueprint

from CTFd.models import Challenges
from CTFd.plugins import override_template, register_plugin_assets_directory
from CTFd.plugins.challenges import BaseChallenge, CHALLENGE_CLASSES


class DigitalChallenge(BaseChallenge):
    id = "digital"
    name = "digital"
    templates = {
        "create": "/plugins/econ_judge/assets/create.html",
        "update": "/plugins/econ_judge/assets/update.html",
        "view": "/plugins/econ_judge/assets/view.html",
    }
    scripts = {
        "create": "/plugins/econ_judge/assets/create.js",
        "update": "/plugins/econ_judge/assets/update.js",
        "view": "/plugins/econ_judge/assets/view.js",
    }
    route = "/plugins/econ_judge/assets/"
    blueprint = Blueprint(
        "econ_judge",
        __name__,
        template_folder="templates",
        static_folder="assets",
    )
    challenge_model = Challenges


_PLUGIN_DIR = os.path.dirname(__file__)


def _read_template(*parts):
    """Read a template file from the plugin's templates/ directory."""
    path = os.path.join(_PLUGIN_DIR, "templates", *parts)
    with open(path, encoding="utf-8") as f:
        return f.read()


def load(app):
    CHALLENGE_CLASSES["digital"] = DigitalChallenge
    register_plugin_assets_directory(app, base_path="/plugins/econ_judge/assets/")

    # Override CTFd's stock templates with the Direction D editorial-minimal
    # designs (s7 login + register). The view.html for the challenge modal
    # is registered via DigitalChallenge.templates above — CTFd Jinja-renders
    # it client-side when the modal opens. The challenges page itself keeps
    # CTFd's stock template (so its Alpine ChallengeBoard wrapper still
    # auto-initialises and the modal flow works); the s2 layout is JS-
    # injected by challenges.js — loaded from THEME_HEADER_CSS, gated to
    # window.location.pathname === '/challenges'.
    override_template("users/login.html",    _read_template("users", "login.html"))
    override_template("users/register.html", _read_template("users", "register.html"))

    from .endpoints import register_endpoints
    register_endpoints(app)
