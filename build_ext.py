#!/usr/bin/env python3
"""Build the JMD C parser and serializer extensions."""
from setuptools import setup, Extension

setup(
    name='jmd_cext',
    ext_modules=[
        Extension(
            'jmd._cparser',
            sources=['jmd/_cparser.c'],
            extra_compile_args=['-O3', '-Wall', '-Wextra'],
        ),
        Extension(
            'jmd._cserializer',
            sources=['jmd/_cserializer.c'],
            extra_compile_args=['-O3', '-Wall', '-Wextra'],
        ),
    ],
)
