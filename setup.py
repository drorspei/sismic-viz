from setuptools import setup
import versioneer


if __name__ == "__main__":
    setup(
        name="sismic-viz",
        version=versioneer.get_version(),
        cmdclass=versioneer.get_cmdclass(),
        description="A simple graphviz visualizer for the python package Sismic",
        long_description=open("README.md").read(),
        author="Dror Speiser",
        url="https://github.com/drorspei/sismic-viz",
        license="MIT",
        classifiers=[
            "Development Status :: 4 - Beta",
            "License :: OSI Approved :: MIT License",
            "Programming Language :: Python :: 3",
        ],
        py_modules=["sismic_viz"],
        python_requires=">=2.7.10",
        install_requires=["sismic", "flask", "future"],
    )
