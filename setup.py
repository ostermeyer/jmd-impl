"""Build configuration for JMD C extensions."""
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

# GCC/Clang flags (Linux, macOS). MSVC has its own switches and already
# applies /O2 /W3 from setuptools' release config, so an empty list
# leaves the defaults intact.
_GNU_COMPILE_ARGS = ["-O3"]
_MSVC_COMPILE_ARGS: list[str] = []

_CPARSER_SOURCES = [
    "jmd/_cparser.c",
    "jmd/_cparser_runtime.c",
    "jmd/_cparser_lex.c",
    "jmd/_cparser_multiline.c",
    "jmd/_cparser_object.c",
    "jmd/_cparser_array.c",
]
_CPARSER_DEPENDS = ["jmd/_cparser_internal.h"]


class OptionalBuildExt(build_ext):
    """Build C extensions, but silently skip if compilation fails.

    Applies compiler-appropriate ``extra_compile_args`` before building
    so the GCC ``-O3`` flag does not break MSVC, which uses ``/O2``.
    """

    def build_extensions(self) -> None:
        """Set compiler-appropriate ``extra_compile_args`` and build."""
        is_msvc = self.compiler.compiler_type == "msvc"
        args = _MSVC_COMPILE_ARGS if is_msvc else _GNU_COMPILE_ARGS
        for ext in self.extensions:
            ext.extra_compile_args = list(args)
        super().build_extensions()

    def build_extension(self, ext):
        """Build the extension, silently skipping if compilation fails."""
        try:
            super().build_extension(ext)
        except Exception:
            pass


setup(
    ext_modules=[
        Extension(
            "jmd._cparser",
            sources=_CPARSER_SOURCES,
            depends=_CPARSER_DEPENDS,
        ),
        Extension("jmd._cserializer", sources=["jmd/_cserializer.c"]),
    ],
    cmdclass={"build_ext": OptionalBuildExt},
)
