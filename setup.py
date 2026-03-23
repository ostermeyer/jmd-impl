"""Build configuration for JMD C extensions."""
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


class OptionalBuildExt(build_ext):
    """Build C extensions, but silently skip if compilation fails."""

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
            sources=["jmd/_cparser.c"],
            extra_compile_args=["-O3"],
        ),
        Extension(
            "jmd._cserializer",
            sources=["jmd/_cserializer.c"],
            extra_compile_args=["-O3"],
        ),
    ],
    cmdclass={"build_ext": OptionalBuildExt},
)
