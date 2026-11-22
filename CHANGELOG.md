# Changelog

## [0.10.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.9.0...v0.10.0) (2026-11-22)


### Features

* **requests:** ping a webhook when a request comes in ([#135](https://github.com/IamMrCupp/benchhud-intake/issues/135)) ([c52d74e](https://github.com/IamMrCupp/benchhud-intake/commit/c52d74e04bcfb20de4caf82256a1ba16e60d7fe4))

## [0.9.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.8.0...v0.9.0) (2026-11-22)


### Features

* **requests:** purge dealt-with requests after a retention window ([#135](https://github.com/IamMrCupp/benchhud-intake/issues/135)) ([b8e7428](https://github.com/IamMrCupp/benchhud-intake/commit/b8e74282e639bbc3acf5e8267bf1c8dc66eec0f6))

## [0.8.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.7.0...v0.8.0) (2026-11-22)


### Features

* **web:** review web requests with accept and dismiss ([#135](https://github.com/IamMrCupp/benchhud-intake/issues/135)) ([e59fd04](https://github.com/IamMrCupp/benchhud-intake/commit/e59fd04dca50ac33312e6c8d08474a1f88cdcf4e))

## [0.7.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.6.0...v0.7.0) (2026-11-22)


### Features

* **api:** accept repair requests through a write-only endpoint ([845f2f7](https://github.com/IamMrCupp/benchhud-intake/commit/845f2f72968e933e92e6fe1509524b45dd282eb0))
* **api:** accept repair requests through a write-only endpoint ([#135](https://github.com/IamMrCupp/benchhud-intake/issues/135)) ([9c8ab99](https://github.com/IamMrCupp/benchhud-intake/commit/9c8ab9909fa084f92703a9f4fb6b03710ef5e903))


### Bug Fixes

* **api:** hold the request endpoint to its contract on odd input ([#135](https://github.com/IamMrCupp/benchhud-intake/issues/135)) ([1dc47fd](https://github.com/IamMrCupp/benchhud-intake/commit/1dc47fd4956ef55ad192ff4ed3e73d58a590837a))

## [0.6.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.5.0...v0.6.0) (2026-11-22)


### Features

* **web:** burnwright look with light, dark and custom themes ([7e86adc](https://github.com/IamMrCupp/benchhud-intake/commit/7e86adc779e36d5d94a9fef1178effaddfd3b29d))
* **web:** burnwright look with light, dark and custom themes ([171b9be](https://github.com/IamMrCupp/benchhud-intake/commit/171b9bea363d0413a2599ecfa190f324c6914cb3))


### Bug Fixes

* **web:** let the intake form submit from a browser ([0895521](https://github.com/IamMrCupp/benchhud-intake/commit/0895521b38f9d3275569bf14a76b7c735a56de00))
* **web:** let the intake form submit from a browser ([3ebfe3e](https://github.com/IamMrCupp/benchhud-intake/commit/3ebfe3e499877a2f92658d292ed3570635aa4d09))

## [0.5.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.4.1...v0.5.0) (2026-11-17)


### Features

* **app:** show a Source link in the footer of every page ([f513a5c](https://github.com/IamMrCupp/benchhud-intake/commit/f513a5cc44732ec1e42d55e6b0e75daf49069f5e))


### Documentation

* **readme:** say the source link wants an http(s) address ([f513a5c](https://github.com/IamMrCupp/benchhud-intake/commit/f513a5cc44732ec1e42d55e6b0e75daf49069f5e))

## [0.4.1](https://github.com/IamMrCupp/benchhud-intake/compare/v0.4.0...v0.4.1) (2026-11-17)


### Bug Fixes

* **license:** relicense to AGPL-3.0-or-later ([2b061cd](https://github.com/IamMrCupp/benchhud-intake/commit/2b061cd1da9331d82d66340ec76b84f88c930244))

## [0.4.0](https://github.com/IamMrCupp/benchhud-intake/compare/v0.3.0...v0.4.0) (2026-11-17)


### Features

* **app:** the packaged app's binary takes the command line's commands ([c87e308](https://github.com/IamMrCupp/benchhud-intake/commit/c87e3086f9bc018372d4cfed00752db0040afbe6))


### Bug Fixes

* **app:** a command's output goes to stdout, not the system log ([3cb6bf9](https://github.com/IamMrCupp/benchhud-intake/commit/3cb6bf95eccab9bcef873738f3aefef2c81d54e7))


### Documentation

* **readme:** the bench apt repo and bench-suite as a way to install ([750eb32](https://github.com/IamMrCupp/benchhud-intake/commit/750eb32f6ee6df0428d8a0612090b742ea7d28db))

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
