from setuptools import find_packages, setup
import os

package_name = 'audio_streamer'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools', 'rclpy'],
    zip_safe=True,
    maintainer='banjov2',
    maintainer_email='guillepallares@gmail.com',
    description='ROS2 node for streaming audio over TCP',
    license='Apache License 2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'audio_streamer = audio_streamer.audio_streamer:main',
        ],
    },
)
