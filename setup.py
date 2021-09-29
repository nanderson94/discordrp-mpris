from pathlib import Path
import re
from setuptools import find_packages, setup

__folder__ = Path(__file__).parent

install_requires = [
    "dbussy",
    "pytoml",
]

test_require = ["pytest", "coverage", "pytest-cov"]


def read(*path_leaves, **kwargs):
    kwargs.setdefault('encoding', "utf-8")
    with Path(__folder__, *path_leaves).open(**kwargs) as f:
        return f.read()


def find_version(*path_leaves):
    version_file = read(*path_leaves)
    version_match = re.search(r"^__version__ = (['\"])(.*?)\1", version_file, re.M)
    if version_match:
        return version_match.group(2)
    else:
        raise RuntimeError("Unable to find version string.")


setup(
    name="discordrp-mpris",
    packages=find_packages("src", exclude=["tests"]),
    package_dir={"": "src"},
    version=find_version("src", "discordrp_mpris", "__init__.py"),
    description="Discord Rich Presence based on mpris2 media players",
    long_description=read("README.md"),
    url="https://github.com/FichteFoll/discordrp-mpris",
    author="FichteFoll",
    author_email="fichtefoll2@googlemail.com",
    license='MIT',
    classifiers=[
        # See https://pypi.python.org/pypi?%3Aaction=list_classifiers
        'Development Status :: 4 - Beta',
        'Environment :: No Input/Output (Daemon)',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: MIT License',
        'Operating System :: Unix',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Topic :: Communications :: Chat :: Discord',  # not in the list above
        # 'Topic :: System',
    ],
    keywords=[
        "discord",
        "discord rich presence",
        "mpris2",
        "media",
        "dbus",
        "mpd",
        "mpv",
        "vlc",
    ],
    entry_points={
        'console_scripts': [
            "discordrp-mpris=discordrp_mpris.__main__:main",
        ],
    },
    package_data={
        'discordrp_mpris.config': [
            "config.toml",
        ]
    },
    install_requires=install_requires,
    tests_require=test_require,
    test_suite="tests",
    # dependency_links=["https://github.com/ldo/dbussy"],
    python_requires=">=3.7",
)
