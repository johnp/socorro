import codecs
import glob
import os

from setuptools import find_packages, setup


# Prevent spurious errors during `python setup.py test`, a la
# http://www.eby-sarna.com/pipermail/peak/2010-May/003357.html:
try:
    import multiprocessing  # noqa
except ImportError:
    pass


def read(fname):
    fpath = os.path.join(os.path.dirname(__file__), fname)
    with codecs.open(fpath, 'r', 'utf8') as f:
        return f.read().strip()


setup(
    name='socorro',
    version='master',
    description=(
        'Socorro is a server to accept and process Breakpad crash reports.'
    ),
    long_description=open('README.rst').read(),
    author='Mozilla',
    author_email='socorro-dev@mozilla.com',
    license='MPL',
    url='https://github.com/mozilla-services/socorro',
    classifiers=[
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: MPL License',
        'Programming Language :: Python :: 2.7',
        'Topic :: Internet :: WWW/HTTP :: WSGI :: Application',
    ],
    keywords=[
        'socorro', 'breakpad', 'crash', 'reporting', 'minidump', 'stacktrace'
    ],
    packages=find_packages(),
    install_requires=[],  # use pip -r requirements.txt instead
    scripts=[
        'socorro-cmd'
    ],
    zip_safe=False,
    data_files=[
        ('socorro/external/postgresql/raw_sql/procs',
            glob.glob('socorro/external/postgresql/raw_sql/procs/*.sql')),
        ('socorro/external/postgresql/raw_sql/views',
            glob.glob('socorro/external/postgresql/raw_sql/views/*.sql')),
        ('socorro/external/postgresql/raw_sql/types',
            glob.glob('socorro/external/postgresql/raw_sql/types/*.sql')),
        ('socorro/siglists', glob.glob('socorro/signature/siglists/*.txt')),
        ('socorro/schemas', glob.glob('socorro/schemas/*.json')),
    ],
)
assert glob.glob('socorro/schemas/*.json')  # TEMP
