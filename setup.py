from setuptools import find_packages
from setuptools import setup

setup(
    name='reorder_python_imports',
    description='Tool for reordering python imports',
    url='https://github.com/asottile/reorder_python_imports',
    version='0.3.5',
    author='Anthony Sottile',
    author_email='asottile@umich.edu',
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
    ],
    packages=find_packages(exclude=('tests*', 'testing*')),
    install_requires=[
        'aspy.refactor_imports>=0.5.3',
        'cached-property',
        'six',
    ],
    entry_points={
        'console_scripts': [
            'reorder-python-imports = reorder_python_imports.main:main',
        ],
    },
)
