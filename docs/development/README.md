# Developing Jentic One

Working on Jentic One itself, from a source checkout. Installing a released
build is covered in the [installation guides](../installation/README.md);
running one in production is [operations](../operations/README.md).

| I want to… | Page |
| ---------- | ---- |
| Set up a checkout and run the stack | [Local development setup](local-setup.md) |
| Run the Python tests, regenerate generated artifacts | [Testing](testing.md) |
| Add a surface, credential provider, or other extension | [Extending Jentic One](extending-jentic-one.md) |
| Understand contexts and how configuration loads | [Context and configuration](context-and-config.md) |
| Cut a release | [Releasing](releasing.md) |
| Judge whether an issue fits the product | [Product scope](product-scope.md) — the rubric the issue-intake harness scores against |
| Publish to AWS Marketplace (seller side) | [AWS Marketplace publishing](marketplace-publishing.md) |

How the system fits together — surfaces, layering, data model, identity — is
in [architecture](../architecture/README.md). Build architecture (images,
charts, Terraform, multi-arch) is in [`deploy/README.md`](../../deploy/README.md).
Contribution mechanics (commits, sign-off, pull requests) are in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md).
