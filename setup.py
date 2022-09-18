import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="iwantitmore",
    version="0.0.1",
    author="Ilya Palopezhentsev",
    author_email="iliks@mail.ru",
    description="Tools for managing personal finances",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ipalopezhentsev/iwantitmore",
    project_urls={
        "Bug Tracker": "https://github.com/ipalopezhentsev/iwantitmore/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "src"},
    packages=setuptools.find_packages(where="src"),
    python_requires=">=3.10",
    install_requires=[
        # TODO: add lower/upper boundary from PEP 440
        "beautifulsoup4>=4.10", "icalendar>=4.0", "requests>=2.27", "SQLAlchemy>=1.4"
    ],
    extras_require={
        "tests": ["pytest", "pytest-cov", "pipdeptree", "pip-tools"],
    },
)
