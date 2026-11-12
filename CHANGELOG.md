# Changelog

## [0.3.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.2.2...v0.3.0) (2026-11-08)


### Features

* **app:** report app name and version on /healthz ([1e18ea9](https://github.com/IamMrCupp/benchhud-intake/commit/1e18ea9b1175bc22c1af9cd0fcbf926d28c8d1f7))
* **app:** report app name and version on /healthz ([a35520a](https://github.com/IamMrCupp/benchhud-intake/commit/a35520a8ff7f2de4971f6d678b4f089085813230))
* **cli:** add set-password --stdin/--generate and new-token --print ([834eb87](https://github.com/IamMrCupp/benchhud-intake/commit/834eb8782bd7dabfc2d5883fbb76eff4ab658de3))
* **cli:** add set-password --stdin/--generate and new-token --print ([d1b6d2e](https://github.com/IamMrCupp/benchhud-intake/commit/d1b6d2e9cacd0f0dc59be37c96829185f2484e48))
* **cli:** serve defaults to port 8401 ([62e6b11](https://github.com/IamMrCupp/benchhud-intake/commit/62e6b11e7d20fa25abe9535112071b1bfd9f41e8))
* **deploy:** move the default host port to 8401, keeping upgraded debs on 8000 ([66a2501](https://github.com/IamMrCupp/benchhud-intake/commit/66a25017feb651b566c4684a2c9e38222d1eca25))
* **deploy:** move the default host port to 8401, keeping upgraded debs on 8000 ([2cb0967](https://github.com/IamMrCupp/benchhud-intake/commit/2cb09670259f9c78168d192b456c50fa88e12247))

## [0.2.2](https://github.com/IamMrCupp/benchhud-intake/compare/v0.2.1...v0.2.2) (2026-10-12)


### Bug Fixes

* **release:** download only the installer artifacts, not the image build record ([7040396](https://github.com/IamMrCupp/benchhud-intake/commit/70403962d8ef634bff6183cc226114140e5d69da))


### Documentation

* **readme:** the tray apps come from releases, not every PR ([3be700e](https://github.com/IamMrCupp/benchhud-intake/commit/3be700e5d12859c72f9054395a763d809bf866da))

## [0.2.1](https://github.com/IamMrCupp/benchhud-intake/compare/v0.2.0...v0.2.1) (2026-09-29)


### Documentation

* **readme:** describe the service's systemd sandbox ([d092cda](https://github.com/IamMrCupp/benchhud-intake/commit/d092cda1c822b46f9f320c77ea3d76d537c32cea))

## [0.2.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.1.0...v0.2.0) (2026-09-26)


### Features

* **cli:** run without a cluster via a serve command, a data folder and a settings file ([768fd14](https://github.com/IamMrCupp/benchhud-intake/commit/768fd14fc8e3580965fd6685b9eb825da58848b7))
* **deb:** a .deb that runs intake as a systemd service ([7cd4c94](https://github.com/IamMrCupp/benchhud-intake/commit/7cd4c9489f2f0285b35799f67af305340bfa50f4))
* **deploy:** a reusable kustomize base for Kubernetes ([82ddd6f](https://github.com/IamMrCupp/benchhud-intake/commit/82ddd6f9bc21cb4382ccb0449aa2ba34e96e7110))
* **image:** versioned release images, a compose file, and serve as the container entrypoint ([1ca0712](https://github.com/IamMrCupp/benchhud-intake/commit/1ca071232534a95a54be3126e11e56a69e1f60c8))
* **tray:** a menu-bar / tray app for macOS and Windows ([84a1540](https://github.com/IamMrCupp/benchhud-intake/commit/84a1540d8512cdda7a11d645df59a643fd6ae65a))


### Bug Fixes

* **projection:** send only the customer's first name to benchhud ([3301700](https://github.com/IamMrCupp/benchhud-intake/commit/3301700d086548d261db8d89d50132a204b58cc7))
* **projection:** send only the customer's first name to benchhud ([771e6be](https://github.com/IamMrCupp/benchhud-intake/commit/771e6be79c7f5d44507510db91f752c3338bd1d2))


### Documentation

* **readme:** point Kubernetes users at the in-repo base ([af50635](https://github.com/IamMrCupp/benchhud-intake/commit/af50635114d718af1e8bffc36e5b3ea70b1a48c5))

## 0.1.0 (2026-09-23)

Baseline: everything on `main` before release automation. Releases after this
one are cut by release-please from conventional commits.
