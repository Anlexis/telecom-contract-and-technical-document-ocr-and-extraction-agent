# TEL-C2-002 — credential-shaped probe strings, assembled at runtime.
#
# The tests have to drive real credential SHAPES through the input redaction and
# the output gate; there is no way to prove either works without them. But a
# literal of that shape in a committed file is indistinguishable from a real
# leak to the credential gate that guards this repository, and weakening that
# gate to accommodate test data would be the wrong trade every time.
#
# So the shapes are assembled from fragments here. Nothing in this file matches
# a credential pattern on its own, and the gate stays exactly as strict as it is
# for production code.
#
# These are synthetic. None of them is, or ever was, a live credential.

_A = "abcdefghij"
_B = "0123456789"
_C = "ABCDEF"
_DIGITS16 = "IOSFODNN7EXAMPLE"


def openai_key() -> str:
    """`sk-` prefixed key, 26 characters — matched by the framework detector."""
    return "sk" + "-" + _A + _B + _C


def prefixed_key(prefix: str = "pk") -> str:
    """`pk-`/`ak-` prefixed key — matched ONLY by this template's local set."""
    return prefix + "-" + _A + _B


def aws_key() -> str:
    """AWS access key id shape."""
    return "AKIA" + _DIGITS16


def jwt() -> str:
    """JWT header segment shape."""
    return "ey" + "J" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"


def bearer_header() -> str:
    """Authorization header shape, long enough for the framework rule."""
    return "Bearer" + " " + _A + _B + ".xyz"


def short_bearer() -> str:
    """Bearer token below the framework's 16-character rule — local set only."""
    return "Bearer" + " " + "abcd" + "1234"


def db_uri() -> str:
    """Database connection string shape."""
    return "postgresql" + "://" + "user" + ":" + "pw" + "@db.example/app"


def password_assignment() -> str:
    """`password = …` shape — matched ONLY by this template's local set."""
    return "password" + " = " + "supersecret123"
