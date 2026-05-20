from flask import Blueprint

from CTFd.models import Challenges
from CTFd.plugins import register_plugin_assets_directory
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


def load(app):
    CHALLENGE_CLASSES["digital"] = DigitalChallenge
    register_plugin_assets_directory(app, base_path="/plugins/econ_judge/assets/")

    from .endpoints import register_endpoints
    register_endpoints(app)
