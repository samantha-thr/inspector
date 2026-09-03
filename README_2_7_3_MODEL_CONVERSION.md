# There Inspector v2.7.3 - Model Conversion Framework

Adds **Browse Library > Model Conversion Lab**.

Current functionality includes Blender detection, single/batch conversion jobs, linked texture discovery, conversion job history, and a Blender headless bridge for OBJ/GLTF/GLB to `.blend`.

The current Inspector parser does not yet reconstruct proprietary There.com `.model` vertices/faces/UVs/materials. Native `.model` jobs therefore report `waiting_for_geometry_decoder` instead of generating corrupt geometry. This release establishes the framework for the native decoder work next.
