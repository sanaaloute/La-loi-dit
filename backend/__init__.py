"""Agentic AI Legal Assistant for Burkina Faso laws and regulations."""

__version__ = "0.1.0"

# Third-party libraries (litellm, pymilvus) call ``dotenv.load_dotenv()`` at
# import time, copying the project ``.env`` into ``os.environ``. Those values
# then take precedence over the ``.env.dev`` overrides that pydantic-settings
# applies when building ``Settings`` — silently flipping local configuration
# (e.g. MILVUS_HOST back to the docker service name). Our Settings load env
# files directly and never rely on ``load_dotenv``, so we disable it for the
# whole process.
import dotenv as _dotenv

_dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: E731

del _dotenv
