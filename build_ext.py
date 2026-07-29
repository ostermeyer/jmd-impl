#!/usr/bin/env python3
"""Build the JMD C parser and serializer extensions.

Sister script to ``setup.py``: invoked directly for in-place development
builds (``python build_ext.py build_ext --inplace``). Surfaces compile
errors instead of swallowing them.
"""
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

# GCC/Clang flags (Linux, macOS). MSVC has its own switches and already
# applies /O2 /W3 from setuptools' release config, so an empty list
# leaves the defaults intact.
_GNU_COMPILE_ARGS = ["-O3", "-Wall", "-Wextra"]
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


class JmdBuildExt(build_ext):
    """Apply compiler-appropriate flags before delegating to setuptools."""

    def build_extensions(self) -> None:
        """Set compiler-appropriate ``extra_compile_args`` and build."""
        is_msvc = self.compiler.compiler_type == "msvc"
        args = _MSVC_COMPILE_ARGS if is_msvc else _GNU_COMPILE_ARGS
        for ext in self.extensions:
            ext.extra_compile_args = list(args)
        super().build_extensions()


setup(
    name="jmd_cext",
    ext_modules=[
        Extension(
            "jmd._cparser",
            sources=_CPARSER_SOURCES,
            depends=_CPARSER_DEPENDS,
        ),
        Extension("jmd._cserializer", sources=["jmd/_cserializer.c"]),
    ],
    cmdclass={"build_ext": JmdBuildExt},
)
