# coding: utf8

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os.path
from decisionlib import *


def main(task_for, mock=False):
    if task_for == "github-pull-request":
        # Pull request.
        android_libs_task = android_libs()
        desktop_linux_libs_task = desktop_linux_libs()
        android_arm32(android_libs_task)

    elif task_for == "github-push":
        # Push to master or a tag.
        android_libs_task = android_libs()
        desktop_linux_libs_task = desktop_linux_libs()

        if CONFIG.git_ref.startswith('refs/tags/'):
            # A release.
            android_arm32_release(android_libs_task)
        else:
            # A regular build.
            android_arm32(android_libs_task)

    else:  # pragma: no cover
        raise ValueError("Unrecognized $TASK_FOR value: %r", task_for)


build_artifacts_expire_in = "1 month"
build_dependencies_artifacts_expire_in = "3 month"
log_artifacts_expire_in = "1 year"

build_env = {
    "RUST_BACKTRACE": "1",
    "RUSTFLAGS": "-Dwarnings",
    "CARGO_INCREMENTAL": "0",
}
linux_build_env = {
    "TERM": "dumb",  # Keep Gradle output sensible.
    "CCACHE": "sccache",
    "RUSTC_WRAPPER": "sccache",
    "SCCACHE_IDLE_TIMEOUT": "1200",
    "SCCACHE_CACHE_SIZE": "40G",
    "SCCACHE_ERROR_LOG": "/build/sccache.log",
    "RUST_LOG": "sccache=info",
}


def android_libs():
    return (
        linux_build_task("Android libs (all architectures): build")
        .with_script("""
            ./scripts/taskcluster-android.sh
            tar -czf /build/repo/target.tar.gz libs/android
        """)
        .with_artifacts(
            "/build/repo/target.tar.gz",
        )
        .find_or_create("build.libs.android." + CONFIG.git_sha_for_directory("libs"))
    )

def desktop_linux_libs():
    return (
        linux_build_task("Desktop libs (Linux): build")
        .with_script("""
            pushd libs && ./build-all.sh desktop && popd
            tar -czf /build/repo/target.tar.gz libs/desktop
        """)
        .with_artifacts(
            "/build/repo/target.tar.gz",
        )
        .find_or_create("build.libs.desktop.linux." + CONFIG.git_sha_for_directory("libs"))
    )

def android_arm32(build_task):
    return (
        linux_build_task("Android (all architectures): build")
        .with_env(BUILD_TASK_ID=build_task)
        .with_dependencies(build_task)
        .with_script("""
            ./automation/taskcluster/curl-artifact.sh ${BUILD_TASK_ID} target.tar.gz | tar -xz
            ./gradlew --no-daemon clean :fxa-client-library:assembleRelease :logins-library:assembleRelease :places-library:assembleRelease
        """)
        .with_artifacts(
            "/build/repo/fxa-client/sdks/android/library/build/outputs/aar/fxaclient-release.aar",
            "/build/repo/logins-api/android/library/build/outputs/aar/logins-release.aar",
            "/build/repo/components/places/android/library/build/outputs/aar/places-release.aar",
        )
        .create()
    )

def android_arm32_release(build_task):
    return (
        linux_build_task("Android (all architectures): build and release")
        .with_env(BUILD_TASK_ID=build_task)
        .with_dependencies(build_task)
        .with_script("""
            ./automation/taskcluster/curl-artifact.sh ${BUILD_TASK_ID} target.tar.gz | tar -xz
            ./gradlew --no-daemon clean :fxa-client-library:assembleRelease :logins-library:assembleRelease :places-library:assembleRelease
            python automation/taskcluster/release/fetch-bintray-api-key.py
            ./gradlew bintrayUpload --debug -PvcsTag="${GIT_SHA}"
        """)
        .with_artifacts(
            "/build/repo/fxa-client/sdks/android/library/build/outputs/aar/fxaclient-release.aar",
            "/build/repo/logins-api/android/library/build/outputs/aar/logins-release.aar",
            "/build/repo/components/places/android/library/build/outputs/aar/places-release.aar",
        )
        .with_scopes("secrets:get:project/application-services/publish")
        .with_features("taskclusterProxy")
        .create()
        # Eventually we can index these releases, if we choose to.
        # .find_or_create("build.android_release." + CONFIG.git_sha)
    )


def dockerfile_path(name):
    return os.path.join(os.path.dirname(__file__), "docker", name + ".dockerfile")


def linux_task(name):
    return DockerWorkerTask(name).with_worker_type("application-services-r")


def linux_build_task(name):
    return (
        linux_task(name)
        # https://docs.taskcluster.net/docs/reference/workers/docker-worker/docs/caches
        .with_scopes("docker-worker:cache:application-services-*")
        .with_caches(**{
            "application-services-cargo-registry": "/root/.cargo/registry",
            "application-services-cargo-git": "/root/.cargo/git",
            "application-services-sccache": "/root/.cache/sccache",
            "application-services-gradle": "/root/.gradle",
            # After we get the docker-in-docker image building working, we can
            # do this instead of baking Rust into our images.
            # "application-services-rustup": "/root/.rustup",
        })
        .with_index_and_artifacts_expire_in(build_artifacts_expire_in)
        .with_artifacts("/build/sccache.log")
        .with_max_run_time_minutes(60)
        .with_dockerfile(dockerfile_path("build"))
        .with_env(**build_env, **linux_build_env)
        .with_repo()
    )


CONFIG.task_name_template = "Application Services: %s"
CONFIG.index_prefix = "project.application-services.application-services"
CONFIG.docker_images_expire_in = build_dependencies_artifacts_expire_in
CONFIG.repacked_msi_files_expire_in = build_dependencies_artifacts_expire_in


if __name__ == "__main__":  # pragma: no cover
    main(task_for=os.environ["TASK_FOR"])
