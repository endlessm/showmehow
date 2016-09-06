# /setup.py
#
# Installation and setup script for showmehow
#
# Copyright (c) 2016 Endless Mobile Inc.
# All rights reserved.
"""Installation and setup script for parse-shebang."""

from setuptools import find_packages, setup

setup(name="showmehow",
      version="0.0.1",
      description="""Show a user how to do something in the terminal.""",
      long_description="""Show a user how to do something in the terminal.""",
      author="Sam Spilsbury",
      author_email="smspillaz@gmail.com",
      classifiers=["Development Status :: 3 - Alpha",
                   "Programming Language :: Python :: 2",
                   "Programming Language :: Python :: 2.7",
                   "Programming Language :: Python :: 3",
                   "Programming Language :: Python :: 3.1",
                   "Programming Language :: Python :: 3.2",
                   "Programming Language :: Python :: 3.3",
                   "Programming Language :: Python :: 3.4",
                   "Intended Audience :: Developers",
                   "Topic :: System :: Shells",
                   "Topic :: Utilities"],
      url="http://github.com/endlessm/showmehow",
      license="MIT",
      keywords="development",
      packages=find_packages(exclude=["test"]),
      install_requires=["setuptools"],
      entry_points={
          "console_scripts": [
              "showmehow=showmehow.showmehow:main",
              "remindmehow=showmehow.remindmehow:main"
          ]
      },
      zip_safe=True,
      include_package_data=True)
