from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension(
            "researchgraph._native_clone",
            sources=["src/researchgraph/_native_clone.c"],
            extra_compile_args=["-std=c11"],
        ),
        Extension(
            "researchgraph.store._native_scan",
            sources=["src/researchgraph/store/_native_scan.c"],
            extra_compile_args=["-std=c11"],
        ),
    ],
)
