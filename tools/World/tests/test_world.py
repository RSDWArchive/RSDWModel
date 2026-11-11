import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from world_source import Resolver, instance_matrix, matrix, normalize_package, transform_bounds, write_json
from world_terrain import decode_tile, vertex_texels
from world_geometry import write_mesh
from world_materials import GEYSER_MASTER, GEYSER_TEXTURES, color_texture_srgb, material_parameters, render_settings, scene_materials, texture_roles
from world_material_audit import audit_materials, uses_adapter
from world_terrain import compare_native_probe
from BuildWorldRegion import checkpoint, inventory
from world_sections import section_cells, identity_digest, terrain_region, build_sections, validate_sections
from world_terrain import bake_color, resize_mask, stitch_boundaries, stitch_normals
from world_terrain import feature_selections, overview_indices, terrain_triangles
from world_materials import water_settings
from world_texture import linear_to_rgb8, sample_bilinear, sample_world_color, srgb_to_linear
from PIL import Image
from world_scene import atmosphere_only, bake_roads, blockout_only, landscape_color_projection, material_descriptors


class WorldTests(unittest.TestCase):
    def test_dune_footprint_sampling_does_not_extend_atlas_outside_bounds(self):
        from AuditDuneCoverage import footprint_coverage
        alpha = np.ones((8,8), dtype=np.float32)
        self.assertEqual(footprint_coverage([[0,0,0],[100,100,1]],[[0,0],[100,100]],alpha)['mean_atlas_alpha'],1)
        self.assertEqual(footprint_coverage([[200,200,0],[300,300,1]],[[0,0],[100,100]],alpha)['mean_atlas_alpha'],0)
        self.assertAlmostEqual(footprint_coverage([[50,0,0],[150,100,1]],[[0,0],[100,100]],alpha)['mean_atlas_alpha'],5/9)

    def test_source_completion_keeps_cleared_candidates_and_detects_omissions(self):
        from AuditWhiteCandidates import build_review
        baseline={'variants':[{'asset':'a','flags':['near_white_surface']},{'asset':'b','flags':[]}]}
        current={'fingerprint':'f','scene_sha256':'s','variants':[{'asset':'a','source':'Mesh', 'placements':1,
                 'materials':['M'],'flags':[],'errors':[],'bookmark':{}}]}
        materials={'M':{'master':'M','family_reviewed':True,'role_findings':[],'bound_textures':{}}}
        result=build_review(baseline,current,materials)
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['summary']['missing_candidates'],['b'])
        self.assertEqual(result['variants'][0]['flags_after'],[])
        self.assertEqual(result['variants'][0]['visual_status'],'manual_review_pending')
        baseline['variants'].pop()
        self.assertEqual(build_review(baseline,current,materials)['status'],'source_review_complete_manual_verification_pending')
        materials['M']['family_reviewed']=False
        self.assertEqual(build_review(baseline,current,materials)['status'],'incomplete')

    def test_disabled_effect_branches_do_not_become_diffuse(self):
        from world_material_audit import surface_role_findings
        p = {'master':'M_NPC_Ghost.0', 'textures':{'MeshOpacityVT':'Wrong_D.0',
             'BaseColor Map':'Color.0','Normal':'Normal.0','NormalVT':'VirtualNormal.0'},
             'switches':{'UseBaseColour':False,'UseNormal':False,'UseVT':False}}
        self.assertEqual(texture_roles(p), {})
        settings = render_settings(p)
        self.assertFalse(settings['base_color_enabled'])
        self.assertFalse(settings['normal_enabled'])
        self.assertEqual(settings['base_color_parameter'], 'Color A')
        p['roles'] = {'base_color':'Wrong_D.0','normal':'Normal.0'}
        self.assertEqual(len(surface_role_findings(p)), 2)
        p['switches'].update(UseBaseColour=True,UseNormal=True,UseVT=True)
        self.assertEqual(texture_roles(p), {'base_color':'Color.0','normal':'VirtualNormal.0'})
        p = {'master':'M_VFX_SW_AnimaDoor_Air_Persist.0','textures':{'Edge_Texture_02':'Smoke_D.0'}}
        self.assertNotIn('base_color', texture_roles(p))
        self.assertEqual(render_settings(p)['base_color_parameter'],'BaseColour')

    def test_shell_fur_disabled_branch_forces_adapter_and_keeps_alpha_separate(self):
        p = {'master':'M_ShellFur.0','textures':{'BaseTexture':'Ram_D.0'},'switches':{'UseTexture?':False}}
        settings = render_settings(p)
        self.assertEqual(settings['family'],'surface_maps')
        self.assertEqual(settings['base_color_parameter'],'MixColor')
        self.assertFalse(settings['base_color_enabled'])
        self.assertNotIn('surface_opacity', settings)
        self.assertNotIn('base_color', texture_roles(p))

    def test_glass_and_anima_opacity_are_explicit_static_proxies(self):
        p = {'master':'M_SimpleGlass.0','textures':{'Normal':'N.0'},'switches':{'UseNormal':False},
             'scalars':{'OpacityMin':0,'OpacityMax':.85}}
        self.assertEqual(texture_roles(p), {})
        self.assertEqual(render_settings(p)['surface_opacity'],0)
        self.assertEqual(render_settings(p)['base_color_parameter'],'TintGlass')
        for master in ('M_AnimaCloudLayer','M_AnimaEnergy_01','M_AnimaEnergy_02'):
            settings=render_settings({'master':master+'.0','scalars':{'Opacity':.05}})
            self.assertEqual(settings['surface_opacity'],.05)
            self.assertIn('not full opacity shader math',settings['approximation'])
        self.assertNotIn('surface_opacity',render_settings({'master':'M_Unknown.0','scalars':{'Opacity':.05}}))

    def test_source_checker_and_highlight_tints_remain_annotated_proxies(self):
        settings=render_settings({'master':'M_Tool_ProcGrid.0','scalars':{'Checker Rough 1':0}})
        self.assertEqual(settings['base_color_parameter'],'Checker Colour 1')
        self.assertEqual(settings['surface_pbr']['roughness'],0)
        self.assertIn('procedural checker',settings['approximation'])
        p={'master':'M_DR_Highlight_Ageing.0','vectors':{'Colour Multiplier':{'R':.3,'G':.2,'B':.1,'A':0}}}
        self.assertEqual(render_settings(p)['surface_color']['multiplier'],[.3,.2,.1])
        self.assertNotIn('surface_opacity',render_settings(p))
        self.assertFalse(render_settings({'master':'M_Character_Standard.0','switches':{'HasEmissive':False}})['emissive_enabled'])

    def test_crystal_source_base_and_normal_do_not_guess_mask_channels(self):
        from world_material_audit import surface_role_findings
        for master in ('M_SW_Crystals', 'M_SW_CrystalsFade'):
            p = {'master':master+'.0','textures':{'Crystal Normal':'Crystal_N.0','Crystal Mask':'Crystal_M.0'},
                 'vectors':{'Base':{'R':.02,'G':.05,'B':.06,'A':.1}},
                 'scalars':{'Metalic':.4,'Roughness':.39,'Emissive':20,'Opasity':.15}}
            self.assertEqual(texture_roles(p), {'normal':'Crystal_N.0'})
            settings = render_settings(p)
            self.assertEqual(settings['base_color_parameter'], 'Base')
            self.assertEqual(settings['surface_pbr'], {'roughness':.39,'metalness':.4})
            self.assertNotIn('opacity', settings)
            self.assertNotIn('emissive_enabled', settings)
            self.assertIn('NOT reconstructed', settings['approximation'])
            self.assertEqual(len(surface_role_findings(p)), 1)
            p['roles'] = texture_roles(p)
            self.assertEqual(surface_role_findings(p), [])
            p['roles']['base_color'] = 'Crystal_M.0'
            self.assertEqual(surface_role_findings(p)[0][0], 'crystal_mask_misassigned')

    def test_opaque_stained_glass_uses_named_surface_maps(self):
        p = {'master':'M_StainedGlassOpaque.0','textures':{'BC':'Glass_BCR.0','N':'Glass_N.0'}}
        self.assertEqual(texture_roles(p), {'base_color':'Glass_BCR.0','normal':'Glass_N.0'})
        self.assertIn('opaque', render_settings(p)['approximation'])
        self.assertNotIn('opacity', render_settings(p))

    def test_heart_slime_uses_unambiguous_hardwired_surface_only(self):
        from world_materials import SLIME_MASTER
        p = {'master':SLIME_MASTER,'textures':{},'referenced_textures':[
            'Textures/T_Heart_Base_BCC','Textures/T_Heart_Base_NRM','Noise_BC.0']}
        self.assertEqual(texture_roles(p), {'base_color':'Textures/T_Heart_Base_BCC','normal':'Textures/T_Heart_Base_NRM'})
        p['referenced_textures'].append('Other/T_Heart_Base_BCC')
        self.assertNotIn('base_color', texture_roles(p))

    def test_notex_pbr_and_dune_review_preserve_source_intent(self):
        p = {'master':'M_Standard_MR_Notex.0','scalars':{'Roughness':0,'Metallic':1}}
        settings = render_settings(p)
        self.assertEqual(settings['base_color_parameter'],'BaseColor')
        self.assertEqual(settings['surface_pbr'],{'roughness':0,'metalness':1})
        dune = render_settings({'master':'M_UMS_Dunes.0'})
        self.assertIn('atlas/fallback fraction',dune['manual_review'])
        self.assertEqual(dune['deformation_review']['status'],'needs_game_reference_and_shader_graph')

    def test_hardwired_texture_roles_are_canonical_before_native_recovery(self):
        from world_materials import VINES_MASTER
        p = {'master':VINES_MASTER, 'textures':{}, 'referenced_textures':['Textures/T_KuldraVines_BCC']}
        mesh = {'Properties':{'StaticMaterials':[{'MaterialInterface':'Material.0'}]}}
        resolver = SimpleNamespace(get=lambda _:mesh, canonical=lambda ref:ref if ref.endswith('.0') else ref+'.0')
        with patch('world_materials.material_parameters', return_value=p):
            result = scene_materials({'assets':[{'source':'Mesh.0','materials':[]}], 'terrain':[]}, resolver)
        self.assertEqual(result['Material.0']['roles']['base_color'],'Textures/T_KuldraVines_BCC.0')

    def test_kuldra_vines_uses_only_unambiguous_named_surface_references(self):
        from world_materials import VINES_MASTER
        p = {'master':VINES_MASTER, 'textures':{}, 'referenced_textures':[
            'Textures/T_KuldraVines_BCC', 'Textures/T_KuldraVines_NRM.0',
            'Textures/T_KuldraVines_M', 'Noise_BC.0']}
        self.assertEqual(texture_roles(p), {'base_color':'Textures/T_KuldraVines_BCC', 'normal':'Textures/T_KuldraVines_NRM.0'})
        self.assertEqual(render_settings(p)['required_texture_roles'], ['base_color','normal'])
        self.assertNotIn('emissive', texture_roles(p))
        p['referenced_textures'].append('Other/T_KuldraVines_BCC')
        self.assertNotIn('base_color', texture_roles(p))
        p['master'] = 'Unknown/M_TunnelVines_01.0'
        self.assertEqual(texture_roles(p), {})

    def test_kuldra_and_masked_surface_aliases_preserve_source_alpha_policy(self):
        from world_material_audit import surface_role_findings
        for master, color, normal in [('M_Kuldra_Basic','BCR','NM'), ('M_BasicMasked','BCA','NR')]:
            p = {'master':master+'.0', 'textures':{color:'SurfaceColor.0', normal:'SurfaceNormal.0'},
                 'properties':{'BlendMode':'EBlendMode::BLEND_Masked', 'OpacityMaskClipValue':.6}}
            self.assertEqual(len(surface_role_findings(p)), 2)
            p['roles'] = texture_roles(p)
            self.assertEqual(surface_role_findings(p), [])
            self.assertEqual(p['roles'], {'base_color':'SurfaceColor.0','normal':'SurfaceNormal.0'})
            self.assertNotIn('alpha_test_enabled', render_settings(p))
            self.assertEqual(render_settings(p)['family'], 'surface_maps')

    def test_withered_recovers_named_surface_not_noise_and_retains_review(self):
        from world_material_audit import surface_role_findings
        p = {'master':'Materials/Crystal/M_Withered.0', 'textures':{
            'RockFractures':'T_WitherSpine_BCC.0', 'Normal Map':'T_WitherSpine_NRM.0',
            'Noise':'Noise_BC.0'}, 'vectors':{'Tint':{'R':.3,'G':.1,'B':.2,'A':0}},
             'scalars':{'Roughness':.7,'Metallic':.7,'Emissive Intensity':.1}}
        p['roles'] = {'normal':'T_WitherSpine_NRM.0'}
        self.assertIn('withered_surface_proxy_missing', [r[0] for r in surface_role_findings(p)])
        p['roles'] = texture_roles(p)
        self.assertEqual(p['roles']['base_color'], 'T_WitherSpine_BCC.0')
        self.assertEqual(surface_role_findings(p), [])
        settings = render_settings(p)
        self.assertEqual(settings['surface_color']['multiplier'], [.3,.1,.2])
        self.assertEqual(settings['surface_pbr'], {'roughness':.7,'metalness':.7})
        self.assertNotIn('emissive', p['roles'])
        self.assertNotIn('emissive_enabled', settings)
        self.assertIn('remain unreconstructed', settings['approximation'])

    def test_standard_tint_is_linear_rgb_not_hex_or_alpha_opacity(self):
        p = {'master':'Materials/M_Standard_Env_MR.0',
             'vectors':{'Color Multiplier':{'R':.12,'G':.10,'B':.08,'A':0,'Hex':'615B4E'}},
             'scalars':{'Brightness':2}}
        settings = render_settings(p)
        self.assertEqual(settings['surface_color']['multiplier'], [.12,.10,.08])
        self.assertEqual(settings['surface_color']['unapplied_brightness'], 2)
        self.assertNotIn('brightness', settings['surface_color'])
        self.assertEqual(settings['surface_color']['review'], 'manual_comparison_required')
        self.assertNotIn('opacity', settings)

    def test_color_proxy_does_not_guess_unrelated_or_specialized_masters(self):
        for master in ('M_Unknown', 'M_Standard_Env_MR_BackgroundMountains', 'M_Standard_Env_MR_PerObjectSand'):
            p = {'master':master+'.0', 'vectors':{'Color Multiplier':{'R':.1}}, 'scalars':{'Brightness':.5}}
            self.assertNotIn('surface_color', render_settings(p))
        self.assertNotIn('surface_color', render_settings({'master':'M_Standard_Env_MR.0'}))

    def test_surface_color_zero_is_not_replaced_with_default(self):
        p = {'master':'M_Standard_Env_MR_Ageing.0', 'vectors':{'Color Multiplier':{'R':0,'G':0,'B':0}},
             'scalars':{'Brightness':0}}
        self.assertEqual(render_settings(p)['surface_color']['unapplied_brightness'], 0)
        self.assertEqual(render_settings(p)['surface_color']['multiplier'], [0,0,0])

    def test_brightness_alone_remains_unapplied_and_annotated(self):
        settings = render_settings({'master':'M_Standard_Env_MR.0','scalars':{'Brightness':6}})
        self.assertNotIn('surface_color', settings)
        self.assertIn('Brightness=6 is unapplied', settings['manual_review'])

    def test_color_proxy_preserves_emissive_and_virtual_branches(self):
        p = {'master':'M_Standard_Env_MR_Emissive.0', 'vectors':{'Color Multiplier':{'R':.5}},
             'switches':{'Is VT':True}, 'scalars':{'IsOff':1}}
        settings = render_settings(p)
        self.assertEqual(settings['family'], 'standard_emissive')
        self.assertFalse(settings['emissive_enabled'])
        self.assertTrue(settings['virtual_texture_enabled'])
        self.assertIn('surface_color', settings)

    def test_color_proxy_forces_adapter_and_keeps_manual_audit(self):
        p = {'master':'M_Standard_Env_MR.0', 'vectors':{'Color Multiplier':{'R':.2,'G':.3,'B':.4}}}
        desc = {'settings':render_settings(p),'textures':{},'master':p['master']}
        asset = {'id':'a','source':'Mesh','native':False,'root':'models','path':'missing.gltf',
                 'materials':['M'],'default_materials':['M'],'section_material_slots':[0]}
        self.assertTrue(uses_adapter(asset, 0, desc))
        with tempfile.TemporaryDirectory() as folder:
            result = audit_materials({'version':'test','assets':[asset],'materials':{'M':desc},
                                      'placements':[{'id':'p','asset':'a','matrix':np.eye(4).tolist()}]}, {'M':p}, folder)
        row = result['materials'][0]
        self.assertEqual(row['status'], 'needs_review')
        self.assertIn('source_color_proxy_manual_review', [i['code'] for i in row['issues']])

    def test_legacy_surface_uses_primary_layer_not_default_blend_mask(self):
        from world_material_audit import surface_role_findings
        params = {'master':'Materials/M_Master.0', 'textures':{
            'BlendMask00':'Materials/DefaultTextures/T_Default_White_D.0',
            'Albedo00':'Pillar_A.0', 'Normal00':'Pillar_N.0',
            'Albedo01':'Inactive_D.0'}, 'switches':{'Layer01?!':False}}
        params['roles'] = {'base_color':params['textures']['BlendMask00']}
        self.assertIn('legacy_primary_surface_mismatch', [r[0] for r in surface_role_findings(params)])
        params['roles'] = texture_roles(params)
        self.assertEqual(params['roles'], {'base_color':'Pillar_A.0', 'normal':'Pillar_N.0'})
        self.assertEqual(surface_role_findings(params), [])
        self.assertIn('unverified', render_settings(params)['approximation'])

    def test_character_surface_uses_active_branch_not_subsurface_mask(self):
        from world_material_audit import surface_role_findings
        params = {'master':'Materials/M_Character_Standard.0', 'textures':{
            'Subsurface_Masks':'Materials/DefaultTextures/T_Default_Linear_Gray_Light_D.0',
            'BaseColor Map':'Hat_BCC.0', 'Normal':'Hat_N.0',
            'BaseColor_VT':'Virtual_BCC.0', 'Normal_VT':'Virtual_N.0'}, 'switches':{'UseVT':False, 'HasOpacity':False}}
        params['roles'] = {'base_color':params['textures']['Subsurface_Masks']}
        self.assertIn('character_surface_branch_mismatch', [r[0] for r in surface_role_findings(params)])
        params['roles'] = texture_roles(params)
        self.assertEqual(params['roles'], {'base_color':'Hat_BCC.0', 'normal':'Hat_N.0'})
        self.assertEqual(surface_role_findings(params), [])
        self.assertFalse(render_settings(params)['alpha_test_enabled'])
        params['switches']['UseVT'] = True
        params['switches']['HasOpacity'] = True
        params['roles'] = texture_roles(params)
        self.assertEqual(params['roles'], {'base_color':'Virtual_BCC.0', 'normal':'Virtual_N.0'})
        self.assertEqual(surface_role_findings(params), [])
        self.assertTrue(render_settings(params)['alpha_test_enabled'])

    def test_enabled_virtual_branch_outranks_regular_defaults(self):
        from world_material_audit import surface_role_findings
        params = {'master':'Materials/M_Standard_Env_MR_Emissive.0', 'textures':{
            'BaseColor':'Materials/DefaultTextures/White.0', 'Normal':'DefaultNormal.0',
            'Virtual BaseColor':'WitherSpine_BCC.0', 'Virtual Normal':'WitherSpine_NRM.0',
            'Virtual Normal Map':'Packed.0'}, 'switches':{'Is VT':True}, 'scalars':{}}
        params['roles'] = {'base_color':params['textures']['BaseColor']}
        self.assertIn('enabled_virtual_texture_branch_mismatch',[r[0] for r in surface_role_findings(params)])
        params['roles'] = texture_roles(params)
        self.assertEqual(params['roles']['base_color'],'WitherSpine_BCC.0')
        self.assertEqual(params['roles']['normal'],'WitherSpine_NRM.0')
        self.assertEqual(surface_role_findings(params),[])
        params['switches']['Use new Packing'] = True
        self.assertEqual(texture_roles(params)['normal'],'Packed.0')
        params['switches']['Is VT'] = False
        self.assertEqual(texture_roles(params)['base_color'],params['textures']['BaseColor'])

    def test_static_decal_recovers_color_alpha_without_guessing_emission(self):
        params = {'master':'Materials/M_Decal.0', 'textures':{'BCO':'Ashes_BCA.0','NR':'Ashes_N.0','Emissive':'Ashes_E.0'},
                  'switches':{'Use R as Alpha':False}, 'scalars':{}}
        self.assertEqual(texture_roles(params), {'base_color':'Ashes_BCA.0','normal':'Ashes_N.0'})
        self.assertEqual(render_settings(params)['family'],'surface_maps')
        self.assertIn('unreconstructed',render_settings(params)['approximation'])

    def test_visual_index_covers_effective_material_variants(self):
        from world_material_audit import write_material_audit
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            assets = [{'id':name, 'source':'Mesh.0', 'layer':'scenery', 'native':False,
                       'root':'models', 'path':'missing.gltf', 'materials':[material],
                       'default_materials':['M1'], 'section_material_slots':[0]}
                      for name,material in [('a','M1'),('b','M2')]]
            placements = [{'id':name, 'asset':aid, 'matrix':np.eye(4).tolist(),
                           'bounds':[[0,0,0],[1,1,1]]} for name,aid in [('p1','a'),('p2','a'),('p3','b')]]
            scene = {'version':'test', 'assets':assets, 'placements':placements,
                     'materials':{'M1':{'textures':{},'settings':{'color_review':'Compare tint in game'}}, 'M2':{'textures':{}}}}
            write_json(root/'WorldRegion.json', scene)
            write_material_audit(SimpleNamespace(out_root=root,version='test'),scene,{})
            visual = json.loads((root/'VisualAuditIndex.json').read_text())
            self.assertEqual(len(visual['variants']),2)
            self.assertEqual([v['placements'] for v in visual['variants']],[2,1])
            self.assertEqual([v['materials'] for v in visual['variants']],[['M1'],['M2']])
            self.assertTrue(all(v['review_status']=='manual_review_pending' for v in visual['variants']))
            self.assertEqual(visual['variants'][0]['review_notes'], [{'material':'M1','notes':['Compare tint in game']}])
            self.assertEqual(visual['variants'][1]['review_notes'], [])

    def test_rock_primary_color_outranks_disabled_detail_default(self):
        from world_material_audit import surface_role_findings
        params = {'master':'Materials/M_Rock.0', 'textures':{
            'Detail Albedo':'Materials/DefaultTextures/T_Default_Gray_D.0',
            'Rock BaseColor':'Textures/T_Cave_Rock_v2_BCC.0',
            'Rock Normal':'Textures/T_Cave_Rock_NRM.0'},
            'switches':{'Use Detail Layer':False}, 'scalars':{}, 'vectors':{}}
        params['roles'] = {'base_color':params['textures']['Detail Albedo']}
        self.assertIn('source_primary_color_mismatch', [f[0] for f in surface_role_findings(params)])
        params['roles'] = texture_roles(params)
        self.assertEqual(params['roles']['base_color'], params['textures']['Rock BaseColor'])
        self.assertEqual(surface_role_findings(params), [])
        self.assertEqual(render_settings(params)['family'], 'rock')
        self.assertFalse(render_settings(params)['emissive_enabled'])

    def test_enabled_rock_detail_keeps_manual_review(self):
        params = {'master':'Materials/M_Rock.0', 'textures':{'Rock BaseColor':'Rock.0'},
                  'switches':{'Use Detail Layer':True}, 'scalars':{}}
        self.assertTrue(render_settings(params)['detail_layer_enabled'])
        self.assertIn('in-game comparison', render_settings(params)['approximation'])

    def test_default_color_is_review_not_an_automatic_error(self):
        from world_material_audit import surface_role_findings
        findings = surface_role_findings({'roles':{'base_color':'Materials/DefaultTextures/T_Default_White_D.0'}})
        self.assertEqual(findings[0][1], 'needs_review')

    def test_section_content_bounds_exclude_hidden_helpers(self):
        from world_sections import content_summary
        result = content_summary([
            {'asset':'rock', 'bounds':[[0,1,2],[3,4,5]]},
            {'asset':'helper', 'bounds':[[-900,-900,-900],[900,900,900]]}],
            {'rock':{'layer':'scenery'}, 'helper':{'layer':'helpers'}})
        self.assertEqual(result['visible_placements'], 1)
        self.assertEqual(result['content_bounds_cm'], {'min':[0,1,2], 'max':[3,4,5]})
        self.assertEqual(content_summary([], {})['content_bounds_cm'], None)

    def test_inventory_honors_scene_capture_only_with_template_overrides(self):
        cases = [({}, {}, False), ({'bVisibleInSceneCaptureOnly':True}, {}, True),
                 ({}, {'bVisibleInSceneCaptureOnly':True}, True),
                 ({'bVisibleInSceneCaptureOnly':False}, {'bVisibleInSceneCaptureOnly':True}, False),
                 ({'bHiddenInSceneCapture':True}, {}, False)]
        for component, template, excluded in cases:
            with self.subTest(component=component, template=template), tempfile.TemporaryDirectory() as folder:
                props = {'StaticMesh':{'ObjectName':"StaticMesh'Mesh'", 'ObjectPath':'Test.3'}, **component}
                rows = [{'Type':'StaticMeshActor','Name':'Actor'},
                        {'Type':'StaticMeshComponent','Name':'Component','Outer':{'ObjectPath':'Test.0'},
                         'Template':{'ObjectPath':'Test.2'},'Properties':props},
                        {'Type':'Object','Name':'Template','Properties':template},
                        {'Type':'StaticMesh','Name':'Mesh','Properties':{
                            'ExtendedBounds':{'Origin':{},'BoxExtent':{'X':1,'Y':1,'Z':1}}}}]
                write_json(Path(folder)/'Test.json', rows)
                placements, _, exclusions, _ = inventory(Resolver(folder), ['Test'],
                    [np.full(3,-100),np.full(3,100)], {'models':[]})
                self.assertEqual(len(placements), 0 if excluded else 1)
                self.assertEqual(len(exclusions), 1 if excluded else 0)
                if excluded:
                    self.assertIn('Scene-capture-only', exclusions[0]['reason'])

    def test_water_depth_requires_positive_source_distance_and_enabled_switch(self):
        params = {'master':'/M_Water.0', 'scalars':{'PDOBlend':16}, 'switches':{'UsePDO?':False}}
        self.assertIsNone(water_settings(params)['depth'])
        params['switches']['UsePDO?'] = True
        self.assertEqual(water_settings(params)['depth'], {'mode':'shoreline_proxy', 'fade_m':.16})
        self.assertEqual(water_settings(params)['opacity'], 1)
        params['scalars']['PDOBlend'] = 0
        self.assertIsNone(water_settings(params)['depth'])

    def test_river_depth_distances_and_shallow_color_use_source_units_and_tint(self):
        params = {'master':'/M_Water_River.0', 'scalars':{'Depth_Distance':256, 'ColorDepth_Distance':128,
                  'Opacity_Shallow':.1}, 'vectors':{'ShalowWaterColor':{'R':.2,'G':.4,'B':.6},
                                                  'Extra Tint':{'R':.5,'G':1,'B':.5}}}
        self.assertEqual(water_settings(params)['depth'], {'mode':'river','fade_m':2.56,'color_m':1.28,
                         'shallow_opacity':.1,'shallow_color':[.1,.4,.3]})

    def test_foam_adapter_is_scoped_to_known_master_and_foam_instances(self):
        p = {'master':'/M_VFX_Master_TransSimple.0','source':'/MI_VFX_WaterfallFoam.0',
             'textures':{'TexA':'Mask.0','TexB':'Unused_BC.0'}, 'scalars':{'OpacityScale':.24,'DepthFade':60},
             'vectors':{'BaseColour':{'R':0,'G':0,'B':0}}, 'switches':{'UseDepthFade':True,'UseBaseColourParameter':False}}
        self.assertEqual(texture_roles(p), {'foam_mask':'Mask.0'})
        settings = render_settings(p)
        self.assertEqual(settings['family'], 'foam')
        self.assertEqual(settings['foam']['depth_fade_m'], .6)
        self.assertEqual(settings['foam']['color'], [1,1,1])
        self.assertEqual(settings['foam']['opacity'], .24)
        p['switches']['UseBaseColourParameter'] = True
        p['switches']['UseDepthFade'] = False
        self.assertEqual(render_settings(p)['foam']['color'], [0,0,0])
        self.assertEqual(render_settings(p)['foam']['depth_fade_m'], 0)
        p['source'] = '/MI_VFX_Smoke.0'
        self.assertNotIn('foam_mask', texture_roles(p))
        p['source'] = '/MI_VFX_WaterfallFoam.0'
        p['master'] = '/Unknown.0'
        self.assertNotIn('foam_mask', texture_roles(p))

    def test_dune_deformation_stays_explicitly_unverified(self):
        p = {'master':'/M_UMS_Dunes.0','scalars':{'GroundVertexSmoothingDistance':300,'Unrelated':1},
             'referenced_functions':['/MF_SampleRVT_Height.0','/Other.0']}
        review = render_settings(p)['deformation_review']
        self.assertEqual(review['parameters'], {'GroundVertexSmoothingDistance':300})
        self.assertEqual(review['source_functions'], ['/MF_SampleRVT_Height.0'])
        self.assertEqual(review['status'], 'needs_game_reference_and_shader_graph')

    def test_water_family_does_not_classify_foam_by_folder(self):
        params = {'master':'/M_VFX_Master_TransSimple.0', 'source':'/Water/Foam.0',
                  'textures':{}, 'scalars':{}, 'vectors':{}, 'properties':{}}
        self.assertNotEqual(render_settings(params).get('family'), 'water')
        params['master'] = '/M_Water.0'
        self.assertEqual(render_settings(params)['family'], 'water')

    def test_shipped_water_plane_is_not_hidden_as_blockout(self):
        asset={'source':'Game/Art/Env/LevelDesign/District1/BlockoutMeshes/SM_BO_Water_Plane.2',
               'materials':['Lake'],'default_materials':['Debug'],'section_material_slots':[0]}
        self.assertFalse(blockout_only(asset, {'Lake':{'settings':{'family':'water'}}}))
        asset['materials']=['Debug']
        self.assertTrue(blockout_only(asset, {'Debug':{'settings':{}}}))

    def test_water_flat_color_and_emission_require_enabled_branch(self):
        params = {'master':'/M_Water.0', 'scalars':{'Roughness':.12,'Opacity':0,'OverallEmissiveStrength':2},
                  'vectors':{'BaseColour':{'R':1,'G':0,'B':0},'ScatteringCoefficientBase':{'R':1,'G':2,'B':0},
                             'AbsorptionCoefficientBase':{'R':3,'G':2,'B':0}}, 'switches':{'UseBaseAsEmissive':True}}
        water = water_settings(params)
        self.assertEqual(water['color'], [.25,.5,0])
        self.assertEqual(water['opacity'], 1)
        self.assertEqual(water['roughness'], .12)
        self.assertEqual(water['emission'], 0)
        params['switches']['UseFlatBaseColour?'] = True
        self.assertEqual(water_settings(params)['color'], [1,0,0])
        self.assertEqual(water_settings(params)['emission'], 2)

    def test_water_roles_restore_river_normals_and_exclude_duckweed(self):
        params = {'master':'/M_Water_River.0','textures':{'Normal1_map':'/n1.0','Normal2_map':'/n2.0',
                  'Duckweed BC':'/Duckweed_BC.0'}}
        self.assertEqual(texture_roles(params), {'normal':'/n1.0','water_normal_2':'/n2.0'})
        params['master'] = '/M_Water_Translucent.0'
        self.assertNotIn('base_color', texture_roles(params))

    def test_water_river_preserves_source_uv_and_deep_color(self):
        water = water_settings({'master':'/M_Water_River.0','scalars':{'Normal1_UTiling':4,'Normal2_Strength':.7,
            'Opacity_Deep':.8,'Roughness':3},'vectors':{'DeepWaterColor':{'G':.5},'Extra Tint':{'R':1,'G':.5,'B':1}}})
        self.assertEqual(water['color'], [0,.25,0])
        self.assertEqual(water['opacity'], .8)
        self.assertEqual(water['roughness'], 1)
        self.assertEqual(water['normal_uv_scales'][0], [4,1])
        self.assertEqual(water['normal_strengths'][1], .7)

    def test_terrain_features_use_only_valid_explicit_ranges(self):
        masks = {'Param_FeatureA_LayerInfo':np.ones((2,2)), 'Param_FeatureB_LayerInfo':np.ones((2,2))}
        params = {'vectors':{'BiomeA_Feature':{'R':2,'G':3,'B':-1,'A':-1}}}
        audit = {}
        selected, used = feature_selections(params, 'A', masks, np.full((2,2),.5), 4, audit)
        self.assertEqual(used, {'Param_FeatureA_LayerInfo'})
        np.testing.assert_allclose(selected[0][0], 2.5)
        self.assertEqual(len(audit['unresolved_feature_ranges']), 1)
        self.assertTrue(audit['feature_blend'][0]['status'].startswith('approximation'))
        self.assertEqual(feature_selections(params,'B',masks,np.zeros((2,2)),4,{}), ([],set()))

    def test_feature_overlay_is_linear_and_does_not_leak_between_biomes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            Image.new('RGB',(2,2),'black').save(root/'base.png')
            Image.new('RGB',(2,2),'white').save(root/'feature.png')
            params={'roles':{'base_color':'array'},'scalars':{},'vectors':{
                'BiomeA_Subbiome_Ranges01':{'R':0,'G':0,'B':0,'A':0},
                'BiomeA_Feature':{'R':1,'G':1,'B':-1,'A':-1},
                'BiomeB_Subbiome_Ranges01':{'R':0,'G':0,'B':0,'A':0}}}
            tile={'material':'M','matrix':np.eye(4).tolist(),'properties':{'ComponentSizeQuads':1}}
            layers={'Param_FeatureA_LayerInfo':np.full((2,2),128)}
            args=(tile,None,layers,{'material_parameters':{'M':params}},root,{'array':{'files':['base.png','feature.png']}})
            np.testing.assert_array_equal(bake_color(*args,size=2),188)
            layers['Biome_B_LayerInfo']=np.full((2,2),255)
            np.testing.assert_array_equal(bake_color(*args,size=2),0)

    def test_overview_preserves_interior_holes_and_visible_quads(self):
        holes=np.zeros((8,8),dtype=bool)
        holes[3,4]=True
        vertices,indices=overview_indices(9,holes)
        np.testing.assert_array_equal(vertices[indices],terrain_triangles(9,holes))
        vertices,indices=overview_indices(9,np.ones((8,8),dtype=bool))
        self.assertEqual(len(indices),0)
        self.assertEqual(len(vertices),4)
        vertices,indices=overview_indices(9,np.zeros((8,8),dtype=bool))
        self.assertEqual(len(indices),6)

    def test_linear_color_blending_and_roundtrip(self):
        encoded = np.arange(256, dtype=np.float32) / 255
        np.testing.assert_array_equal(linear_to_rgb8(srgb_to_linear(encoded)), np.arange(256))
        self.assertEqual(int(linear_to_rgb8(np.array(0.5))), 188)

    def test_mask_resampling_tracks_vertices_not_image_pixel_centers(self):
        mask = np.tile(np.array([0, 128, 255], dtype=np.uint8), (3, 1))
        result = resize_mask(mask, 5)
        np.testing.assert_allclose(result[0], np.array([0, 64, 128, 191.5, 255]) / 255)
        np.testing.assert_allclose(result[:, -1], 1)

    def test_periodic_bilinear_filter_wraps_both_sides(self):
        image = np.array([[0., 1.], [0., 1.]])
        result = sample_bilinear(image, np.array([-.5, 1.5, -2.5]), np.zeros(3), repeat=True)
        np.testing.assert_allclose(result, .5)

    def test_terrain_mip_filter_averages_in_linear_light(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'checker.png'
            Image.fromarray(np.tile(np.array([[0, 255], [255, 0]], dtype=np.uint8)[:, :, None], (1, 1, 3))).save(path)
            result = sample_world_color(path, np.zeros((2,2)), np.zeros((2,2)), footprint_cm=400)
            np.testing.assert_allclose(result, .5)

    def test_missing_subbiome_a_does_not_dilute_painted_b(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB', (2,2), (255,0,0)).save(root/'a.png')
            Image.new('RGB', (2,2), (0,0,255)).save(root/'b.png')
            params = {'roles':{'base_color':'array'}, 'scalars':{}, 'vectors':{
                'BiomeA_Subbiome_Ranges01':{'R':0,'G':0,'B':1,'A':1},
                'BiomeA_Subbiome_Ranges02':{'R':-1,'G':-1}}}
            tile = {'material':'M','matrix':np.eye(4).tolist(),'properties':{'ComponentSizeQuads':1}}
            layers = {'SubBiome_B_LayerInfo':np.full((2,2),255)}
            audit = {}
            result = bake_color(tile,None,layers,{'material_parameters':{'M':params}},root,
                                {'array':{'files':['a.png','b.png']}},size=2,audit=audit)
            np.testing.assert_array_equal(result,np.tile([0,0,255],(2,2,1)))
            self.assertTrue(audit['missing_subbiome_a'])
            self.assertEqual(audit['subbiome_unpainted_fallback_fraction'],0)

    def test_missing_level_is_zero_not_midpoint_and_unknown_paint_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB', (2,2), (0,0,0)).save(root/'a.png')
            Image.new('RGB', (2,2), (255,255,255)).save(root/'b.png')
            params = {'roles':{'base_color':'array'}, 'scalars':{}, 'vectors':{
                'BiomeA_Subbiome_Ranges01':{'R':0,'G':1,'B':-1,'A':-1},
                'BiomeA_Subbiome_Ranges02':{'R':-1,'G':-1}}}
            tile = {'material':'M','matrix':np.eye(4).tolist(),'properties':{'ComponentSizeQuads':1}}
            layers = {'Param_FeatureA_LayerInfo':np.full((2,2),255)}
            audit = {}
            result = bake_color(tile,None,layers,{'material_parameters':{'M':params}},root,
                                {'array':{'files':['a.png','b.png']}},size=2,audit=audit)
            np.testing.assert_array_equal(result,0)
            self.assertEqual(audit['active_unmodeled_layers'],['Param_FeatureA_LayerInfo'])
            layers['Param_Level_LayerInfo'] = np.full((2,2),128)
            result = bake_color(tile,None,layers,{'material_parameters':{'M':params}},root,
                                {'array':{'files':['a.png','b.png']}},size=2)
            np.testing.assert_array_equal(result,188)

    def test_absent_weight_channels_equal_explicit_zero_channels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB',(2,2),(255,0,0)).save(root/'a.png')
            Image.new('RGB',(2,2),(0,255,0)).save(root/'b.png')
            params = {'roles':{'base_color':'array'}, 'scalars':{}, 'vectors':{
                'BiomeA_Subbiome_Ranges01':{'R':0,'G':0,'B':0,'A':0},
                'BiomeB_Subbiome_Ranges01':{'R':1,'G':1,'B':1,'A':1}}}
            tile = {'material':'M','matrix':np.eye(4).tolist(),'properties':{'ComponentSizeQuads':1}}
            layers = {'Biome_B_LayerInfo':np.full((2,2),255),'SubBiome_B_LayerInfo':np.full((2,2),255)}
            args = (tile,None,layers,{'material_parameters':{'M':params}},root,{'array':{'files':['a.png','b.png']}})
            omitted = bake_color(*args,size=3)
            for name in ('Biome_A_LayerInfo','SubBiome_A_LayerInfo','Param_Level_LayerInfo'):
                layers[name] = np.zeros((2,2),dtype=np.uint8)
            explicit = bake_color(*args,size=3)
            np.testing.assert_array_equal(omitted,explicit)
            np.testing.assert_array_equal(explicit,np.tile([0,255,0],(3,3,1)))

    def test_out_of_range_biome_selection_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB',(2,2),'white').save(root/'color.png')
            params = {'roles':{'base_color':'array'},'vectors':{},'scalars':{}}
            tile = {'material':'M','matrix':np.eye(4).tolist(),'properties':{'ComponentSizeQuads':1}}
            audit = {}
            bake_color(tile,None,{}, {'material_parameters':{'M':params}},root,
                       {'array':{'files':['color.png']}},size=2,audit=audit)
            self.assertEqual(audit['range_assumptions'],['A'])
            self.assertEqual(audit['clamped_ranges'],[{'biome':'A','range':[1,4]}])

    def test_cave_tint_is_linear_not_srgb(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB',(2,2),'white').save(root/'color.png')
            params = {'roles':{f'cave_layer_{i}':'color' for i in range(1,4)},
                      'vectors':{},'scalars':{'L1_Brightness':.25}}
            tile = {'material':'M','matrix':np.eye(4).tolist(),'properties':{'ComponentSizeQuads':1}}
            result = bake_color(tile,None,{}, {'material_parameters':{'M':params}},root,
                                {'color':{'file':'color.png'}},size=2)
            np.testing.assert_array_equal(result,137)

    def test_normals_weld_only_within_same_landscape(self):
        world = np.array([[[0.,0,0],[1,0,0]],[[0,1,0],[1,1,0]]])
        a = np.tile([0.,1,0],(2,2,1))
        b = np.tile([1.,1,0],(2,2,1))
        c = np.tile([0.,0,1],(2,2,1))
        tiles = [{'landscape_guid':name,'material':'M'} for name in ('one','one','two')]
        report = stitch_normals(tiles,[world,world.copy(),world.copy()],[a,b,c])
        np.testing.assert_allclose(a,b)
        np.testing.assert_allclose(a,np.tile(np.array([1,2,0])/np.sqrt(5),(2,2,1)))
        np.testing.assert_array_equal(c,np.tile([0,0,1],(2,2,1)))
        self.assertAlmostEqual(report['raw_max_angle_degrees'],45)

    def test_normal_map_conversion_is_lossless(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'source-data/native').mkdir(parents=True)
            pixels = np.arange(192,dtype=np.uint8).reshape(8,8,3)
            Image.fromarray(pixels).save(root/'source-data/native/n.png')
            params = {'roles':{'normal':'N'},'textures':{},'vectors':{},'scalars':{},'properties':{}}
            descriptors = material_descriptors(SimpleNamespace(out_root=root),
                {'material_parameters':{'M':params},'region':{'workers':1}},
                [{'source':'N','kind':'texture','file':'n.png'}])
            with Image.open(root/descriptors['M']['textures']['normal']) as image:
                np.testing.assert_array_equal(np.asarray(image),pixels)

    def test_linear_source_color_is_encoded_once_and_keeps_alpha(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'source-data/native').mkdir(parents=True)
            Image.new('RGBA',(8,8),(128,128,128,73)).save(root/'source-data/native/color.png')
            params = {'roles':{'base_color':'C','normal':'C'},'textures':{},'vectors':{},'scalars':{},
                      'properties':{},'texture_srgb':{'C':False}}
            descriptors = material_descriptors(SimpleNamespace(out_root=root),
                {'material_parameters':{'M':params},'region':{'workers':1}},
                [{'source':'C','kind':'texture','file':'color.png'}])
            result = descriptors['M']
            self.assertEqual(result['linear_color_sources'],{'base_color':'C'})
            self.assertNotEqual(result['textures']['base_color'],result['textures']['normal'])
            with Image.open(root/result['textures']['base_color']) as image:
                pixel = image.convert('RGBA').getpixel((0,0))
                self.assertLessEqual(abs(pixel[0]-188),2)
                self.assertEqual(pixel[3],73)
            with Image.open(root/result['textures']['normal']) as image:
                self.assertEqual(image.convert('RGBA').getpixel((0,0)),(128,128,128,73))

    def test_terrain_honors_linear_source_texture_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB',(2,2),(128,128,128)).save(root/'color.png')
            params = {'roles':{'base_color':'C'},'vectors':{},'scalars':{},'texture_srgb':{'C':False}}
            tile = {'material':'M','matrix':np.eye(4).tolist(),'properties':{'ComponentSizeQuads':1}}
            result = bake_color(tile,None,{}, {'material_parameters':{'M':params}},root,
                                {'C':{'file':'color.png'}},size=2)
            np.testing.assert_array_equal(result,188)

    def test_material_color_flags_come_from_texture_source_not_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root/'RSDragonwilds/Content/Color.json',[{'Name':'Color','Properties':{'SRGB':False}}])
            write_json(root/'RSDragonwilds/Content/Default.json',[{'Name':'Default','Properties':{}}])
            params = {'roles':{'base_color':'RSDragonwilds/Content/Color.0',
                              'emissive':'RSDragonwilds/Content/Default.0'}}
            self.assertEqual(color_texture_srgb(params,Resolver(root)),
                {'RSDragonwilds/Content/Color.0':False,'RSDragonwilds/Content/Default.0':True})

    def test_linear_color_correction_uses_local_material_adapter(self):
        asset = {'native':False,'materials':[],'default_materials':['M']}
        self.assertFalse(uses_adapter(asset,0,{}))
        self.assertTrue(uses_adapter(asset,0,{'linear_color_sources':{'base_color':'C'}}))

    def test_parallel_road_texture_write_preserves_unpainted_colors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tiles = []
            for i, color in enumerate(((8,31,128),(110,140,160))):
                Image.new('RGB',(8,8),color).save(root/f'{i}.png')
                tiles.append({'path':f'{i}.gltf','overview_path':f'{i}-overview.gltf',
                              'bounds':[[i*100,0,0],[(i+1)*100,100,10]]})
            report, baked = bake_roads(SimpleNamespace(out_root=root),
                {'assets':[],'placements':[],'material_parameters':{},'region':{'workers':2}},tiles,{})
            self.assertEqual(report,[])
            self.assertFalse(baked)
            for i, color in enumerate(((8,31,128),(110,140,160))):
                with Image.open(root/f'{i}.png') as image:
                    self.assertEqual(image.getpixel((0,0)),color)
                self.assertTrue((root/f'{i}-overview.webp').is_file())
                self.assertTrue((root/f'{i}-base.png').is_file())

    def test_unreal_rotator_signs(self):
        np.testing.assert_allclose((matrix(rotation={"Yaw":90}) @ [1,0,0,1])[:3], [0,1,0], atol=1e-12)
        np.testing.assert_allclose((matrix(rotation={"Pitch":90}) @ [1,0,0,1])[:3], [0,0,1], atol=1e-12)
        np.testing.assert_allclose((matrix(rotation={"Roll":90}) @ [0,1,0,1])[:3], [0,0,-1], atol=1e-12)

    def test_quaternion_and_nonuniform_negative_scale(self):
        m=instance_matrix({"TransformData":{"Rotation":{"Z":2**-.5,"W":2**-.5},"Translation":{"X":10},"Scale3D":{"X":-2,"Y":3,"Z":4}}})
        np.testing.assert_allclose((m@[1,1,1,1])[:3],[7,-2,4],atol=1e-12)
        self.assertLess(np.linalg.det(m),0)

    def test_bounds_with_rotation(self):
        actual=transform_bounds([[0,0,0],[100,200,300]],matrix(rotation={"Yaw":90}))
        np.testing.assert_allclose(actual,[[-200,0,0],[0,100,300]],atol=1e-10)

    def test_mount_normalization(self):
        self.assertEqual(normalize_package('/Game/Maps/World/L_World'),'RSDragonwilds/Content/Maps/World/L_World')
        self.assertEqual(normalize_package('/DowdunReach/Art/Rock'),'RSDragonwilds/Plugins/GameFeatures/DowdunReach/Content/Art/Rock')
        self.assertEqual(normalize_package('Engine/Plugins/Experimental/Landmass/Content/Test'),
                         'Engine/Plugins/Experimental/Landmass/Content/Test')

    def test_engine_plugin_mount_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            package = 'Engine/Plugins/Experimental/Landmass/Content/Test'
            write_json(Path(folder)/(package+'.json'), [{'Name':'Test','Properties':{}}])
            self.assertEqual(Resolver(folder).canonical('/Landmass/Test.Test'), package+'.0')

    def test_section_boundaries_negative_cells_and_spanning(self):
        self.assertEqual(section_cells([[-1,-1,0],[1,1,1]],100),[(-1,-1),(-1,0),(0,-1),(0,0)])
        self.assertEqual(section_cells([[0,0,0],[10000,10000,1]],100),[])
        self.assertEqual(identity_digest(['b','a']),identity_digest(['a','b']))
        self.assertNotEqual(identity_digest(['a','b']),identity_digest(['a','c']))
        self.assertEqual(terrain_region('/MI_Cave_Landscape.0'),'Caves')

    def test_cave_landscape_uses_painted_layer(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,color in [('rock',(255,0,0)),('dirt',(0,255,0)),('mud',(0,0,255))]:
                Image.new('RGB',(2,2),color).save(root/(name+'.png'))
            roles={f'cave_layer_{i}':name for i,name in enumerate(('rock','dirt','mud'),1)}
            plan={'material_parameters':{'M':{'roles':roles,'vectors':{},'scalars':{}}}}
            tile={'material':'M','matrix':np.eye(4).tolist(),'properties':{'ComponentSizeQuads':1}}
            textures={name:{'file':name+'.png'} for name in ('rock','dirt','mud')}
            result=bake_color(tile,None,{'Dirt01_LayerInfo':np.full((2,2),255)},plan,root,textures,size=2)
            np.testing.assert_array_equal(result,np.tile([0,255,0],(2,2,1)))

    def test_standard_emission_respects_off_scalar(self):
        params={'master':'M_Standard_Env_MR_Emissive.0','textures':{'Emissive':'Glow.0'},'scalars':{}}
        self.assertEqual(texture_roles(params)['emissive'],'Glow.0')
        params['scalars']['IsOff']=1
        self.assertFalse(render_settings(params)['emissive_enabled'])
        self.assertNotIn('emissive',texture_roles(params))

    def test_only_explicit_atmosphere_materials_are_excluded(self):
        asset={'section_material_slots':[0], 'materials':[], 'default_materials':['Fog']}
        materials={'Fog':{'master':'Path/M_LocalFogV2.0'}, 'Solid':{'master':GEYSER_MASTER}}
        self.assertTrue(atmosphere_only(asset,materials))
        asset['section_material_slots']=[0,1]
        asset['default_materials'].append('Solid')
        self.assertFalse(atmosphere_only(asset,materials))

    def test_edge_patch_blends_only_same_landscape_and_reports_raw_difference(self):
        a=np.array([[[0.,0,10],[1,0,10]],[[0,1,10],[1,1,10]]])
        b=a.copy();b[:,:,2]=30
        c=a.copy();c[:,:,2]=80
        tiles=[{'source':'a','landscape_guid':'one','material':'M'},
               {'source':'b','landscape_guid':'one','material':'M'},
               {'source':'c','landscape_guid':'two','material':'M'}]
        report=stitch_boundaries(tiles,[a,b,c])
        np.testing.assert_array_equal(a[:,:,2],20)
        np.testing.assert_array_equal(b[:,:,2],20)
        np.testing.assert_array_equal(c[:,:,2],80)
        self.assertEqual(report['raw_max_border_delta_cm'],20)
        self.assertEqual(len(report['adjusted_boundaries']),4)

    def test_section_coverage_shared_filter_and_corruption_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            scene={'version':'test','coordinates':'test','region':{'section_size_cm':100},
                   'assets':[{'id':name,'source':name,'materials':[],'default_materials':['M'],
                              'section_material_slots':[0], 'layer':'scenery'} for name in ('active','road')],
                   'materials':{'M':{}},'terrain':[{'source':'T','material':'MI_World.0',
                                                  'bounds':[[0,0,0],[100,100,5]]}],
                   'placements':[{'id':'p','asset':'active','bounds':[[-1,0,0],[1,2,3]]},
                                 {'id':'s','asset':'active','bounds':[[0,0,0],[10000,10000,1]]},
                                 {'id':'baked','asset':'road','bounds':[[0,0,0],[1,1,1]],'baked_road':True}]}
            write_json(root/'WorldRegion.json',scene)
            import hashlib
            digest=hashlib.sha256((root/'WorldRegion.json').read_bytes()).hexdigest()
            write_json(root/'MaterialAudit.json',{'scene_sha256':digest,'materials':[]})
            write_json(root/'WorldRegionReport.json',{'excluded':[]})
            args=SimpleNamespace(out_root=root)
            build_sections(args)
            validate_sections(args)
            shared=json.loads((root/'WorldShared.json').read_text())
            self.assertEqual([a['id'] for a in shared['assets']],['active'])
            spanning=json.loads((root/'SpanningPlacements.json').read_text())
            spanning[0]['id']='wrong-identity-same-count'
            write_json(root/'SpanningPlacements.json',spanning)
            with self.assertRaisesRegex(ValueError,'identity digest'):
                validate_sections(args)

    def test_landscape_projection_uses_source_ground_color_and_records_approximation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'assets/materials').mkdir(parents=True)
            Image.new('RGB',(8,8),(150,90,40)).save(root/'ground.png')
            material={'master':'P/M_UMS_Dunes.0','textures':{},'texture_gaps':[{'role':'base_color'}]}
            landscape_color_projection(SimpleNamespace(out_root=root),
                [{'material':'P/MI_World_Landscape.0','bounds':[[0,0,0],[100,100,10]],'road_texture':'ground.png'}],{'M':material},size=16)
            self.assertEqual(material['projection'],'landscape_color')
            self.assertEqual(material['landscape_bounds_cm'],[[0,0],[100,100]])
            self.assertIn('procedural normals',material['approximation'])
            with Image.open(root/material['textures']['landscape_color']) as image:
                self.assertLess(abs(image.getpixel((8,8))[0]-150),5)

    def test_basic_shape_with_ground_material_is_not_a_blockout(self):
        asset={'source':'Engine/Content/BasicShapes/Plane.2','materials':[],
               'default_materials':['Ground'],'section_material_slots':[0]}
        self.assertFalse(blockout_only(asset,{'Ground':{'master':'P/M_SW_Ground.0'}}))
        self.assertTrue(blockout_only(asset,{'Ground':{'master':'Engine/BasicShapeMaterial.0'}}))
        asset['source']='Game/Developers/Artist/Prototype/Models/Backdrop.0'
        self.assertTrue(blockout_only(asset,{}))
        asset['source']='Game/Art/Env/LevelDesign/District1/BlockoutMeshes/Shape.0'
        self.assertTrue(blockout_only(asset,{}))
        asset['source']='Game/Art/Env/Models/Temple.0'
        self.assertFalse(blockout_only(asset,{}))

    def test_identity_templates_attachments_and_absolute_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            write_json(Path(folder)/'RSDragonwilds/Content/Test.json',[
                {"Name":"Root","Properties":{"RelativeLocation":{"X":10},"RelativeRotation":{"Yaw":90},"RelativeScale3D":{"X":2,"Y":2,"Z":2}}},
                {"Name":"Same","Properties":{"AttachParent":{"ObjectPath":"RSDragonwilds/Content/Test.0"},"RelativeLocation":{"Y":3}}},
                {"Name":"Same","Properties":{"AttachParent":{"ObjectPath":"RSDragonwilds/Content/Test.0"},"RelativeLocation":{"Y":3},"bAbsoluteLocation":True,"bAbsoluteRotation":True,"bAbsoluteScale":True}},
                {"Name":"Template","Properties":{"RelativeScale3D":{"X":3,"Y":3,"Z":3},"StaticMesh":"test"}},
                {"Name":"Instance","Template":{"ObjectPath":"RSDragonwilds/Content/Test.3"},"Properties":{"RelativeLocation":{"Z":4}}},
            ])
            r=Resolver(folder)
            np.testing.assert_allclose(r.transform('RSDragonwilds/Content/Test.1')[:3,3],[4,0,0],atol=1e-12)
            np.testing.assert_allclose(r.transform('RSDragonwilds/Content/Test.2'),matrix(location={"Y":3}))
            self.assertEqual(r.effective('RSDragonwilds/Content/Test.4')['StaticMesh'],'test')
            self.assertEqual(r.transform('RSDragonwilds/Content/Test.4')[2,2],3)
            with self.assertRaisesRegex(ValueError,'Ambiguous'):
                r.get('RSDragonwilds/Content/Test.Same')

    def test_cycles_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            write_json(Path(folder)/'RSDragonwilds/Content/Test.json',[{"Name":"Root","Properties":{"AttachParent":{"ObjectPath":"RSDragonwilds/Content/Test.0"}}}])
            with self.assertRaisesRegex(ValueError,'cycle'):
                Resolver(folder).transform('RSDragonwilds/Content/Test.0')

    def test_subsection_duplicate_edges(self):
        np.testing.assert_equal(vertex_texels(126,63,2)[[0,62,63,64,125,126]],[0,62,64,65,126,127])

    def test_packed_heights_uv_offsets_and_hole_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            raw=np.zeros((4,4,4),dtype=np.uint8)
            # BGRA, height 32769 gives 1/128 of a local unit.
            raw[:,:,2]=128;raw[:,:,1]=1
            raw.tofile(root/'height.bgra')
            weights=np.zeros((2,2,4),dtype=np.uint8);weights[0,0,0]=255
            weights.tofile(root/'weight.bgra')
            textures={"P.0":{"source":"P.0","raw":"height.bgra","format":"PF_B8G8R8A8","width":4,"height":4},
                      "P.1":{"source":"P.1","raw":"weight.bgra","format":"PF_B8G8R8A8","width":2,"height":2}}
            tile={"properties":{"ComponentSizeQuads":1,"SubsectionSizeQuads":1,"NumSubsections":1,
                "HeightmapTexture":{"ObjectPath":"P.0"},"HeightmapScaleBias":{"Z":0.5,"W":0.5},
                "WeightmapTextures":[{"ObjectPath":"P.1"}],"WeightmapLayerAllocations":[{
                    "LayerInfo":{"ObjectPath":"LandscapeVisibilityLayerInfo.0"},"WeightmapTextureIndex":0,"WeightmapTextureChannel":2}]}}
            heights,packed,layers=decode_tile(tile,root,textures)
            np.testing.assert_equal(packed,np.full((2,2),32769))
            np.testing.assert_allclose(heights,np.full((2,2),1/128))
            self.assertEqual(layers['LandscapeVisibilityLayerInfo'][0,0],255)

    def test_mesh_winding_y_up(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'terrain.gltf'
            doc=write_mesh(path,[[0,0,0],[1,0,0],[0,0,1],[1,0,1]],[0,2,3,0,3,1])
            normal_view=doc['bufferViews'][1]
            normals=np.frombuffer(path.with_suffix('.bin').read_bytes(),dtype='<f4',count=12,offset=normal_view['byteOffset']).reshape(-1,3)
            np.testing.assert_allclose(normals,np.tile([0,1,0],(4,1)))

    def test_material_sections_are_not_material_slots(self):
        mesh = {"Properties": {"StaticMaterials": [{"MaterialInterface": {"ObjectPath": "M.0"}},
                                                   {"MaterialInterface": {"ObjectPath": "M.1"}}]},
                "RenderData": {"LODs": [{"Sections": [{"MaterialIndex": 1, "NumTriangles": 10},
                                                         {"MaterialIndex": 0, "NumTriangles": 5}]}]}}
        asset = {"source": "Mesh.0", "materials": []}
        with patch("world_materials.material_parameters", return_value={"textures": {}}):
            scene_materials({"assets": [asset], "terrain": []}, SimpleNamespace(get=lambda _: mesh))
        self.assertEqual(asset["default_materials"], ["M.0", "M.1"])
        self.assertEqual(asset["section_material_slots"], [1, 0])

    def test_native_probe_comparison_gate(self):
        positions = np.array([[1, 0.1, 0], [1.01, 0.2, 0], [1, 0.3, 0.01], [1.01, 0.4, 0.01]])
        tile = {"matrix": np.diag([100, 100, 100, 1]).tolist()}
        with patch("world_terrain.read_glb_mesh", return_value=[{"positions": positions}]), \
             patch("world_terrain.decode_tile", return_value=(np.array([[10, 20], [30, 40]]), None, None)):
            result = compare_native_probe(tile, {"file": "test.glb"}, Path("."), {})
            self.assertEqual(result["status"], "passed")
            positions[0, 1] += 1
            with self.assertRaisesRegex(ValueError, "exceeds 1cm"):
                compare_native_probe(tile, {"file": "test.glb"}, Path("."), {})

    def test_running_stage_is_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(out_root=Path(folder))
            run = {"status": "running", "stages": {}}
            checkpoint(args, run, "terrain", "running")
            saved = json.loads((args.out_root/"PipelineRun.json").read_text())
            self.assertEqual(saved["stages"]["terrain"], "running")
            self.assertEqual(saved["status"], "running")

    def test_material_static_switch_inheritance_and_surface_override_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            package = "RSDragonwilds/Content/M_Foliage_Small"
            info = {"Name": "CanBeEmissive"}
            write_json(Path(folder)/(package+".json"), [
                {"Type": "Material", "Name": "M_Foliage_Small", "Properties": {
                    "BlendMode": "EBlendMode::BLEND_Masked", "TwoSided": True},
                 "CachedExpressionData": {"RuntimeEntries[8]": {"ParameterInfoSet": [info]}, "StaticSwitchValues": [False]}},
                {"Name": "Enabled", "Properties": {"Parent": {"ObjectPath": package+".0"},
                    "StaticParametersRuntime": {"StaticSwitchParameters": [{"ParameterInfo": info, "Value": True, "bOverride": True}]},
                    "BasePropertyOverrides": {"OpacityMaskClipValue": 0.12, "bOverride_OpacityMaskClipValue": True,
                                              "TwoSided": False, "bOverride_TwoSided": False}}},
                {"Name": "Inherited", "Properties": {"Parent": {"ObjectPath": package+".1"},
                    "StaticParametersRuntime": {"StaticSwitchParameters": [{"ParameterInfo": info, "Value": False, "bOverride": False}]}}},
                {"Name": "Disabled", "Properties": {"Parent": {"ObjectPath": package+".2"},
                    "StaticParametersRuntime": {"StaticSwitchParameters": [{"ParameterInfo": info, "Value": False, "bOverride": True}]}}},
            ])
            r = Resolver(folder)
            root = material_parameters(r, package+".0")
            child = material_parameters(r, package+".2")
            disabled = material_parameters(r, package+".3")
            self.assertFalse(root["switches"]["CanBeEmissive"])
            self.assertTrue(child["switches"]["CanBeEmissive"])
            self.assertFalse(disabled["switches"]["CanBeEmissive"])
            self.assertTrue(child["properties"]["TwoSided"])
            self.assertEqual(child["properties"]["OpacityMaskClipValue"], 0.12)
            self.assertEqual(render_settings(child)["family"], "foliage")

    def test_emissive_texture_requires_enabled_source_switch(self):
        p = {"textures": {"BaseColor": "LeafBC.0", "EmissiveMap": "LeafGlow.0"},
             "switches": {"CanBeEmissive": False}}
        self.assertNotIn("emissive", texture_roles(p))
        p["switches"]["CanBeEmissive"] = True
        self.assertEqual(texture_roles(p)["emissive"], "LeafGlow.0")
        p["textures"]["EmissiveMap"] = "Engine/Content/EngineResources/DefaultTexture.0"
        self.assertNotIn("emissive", texture_roles(p))

    def test_parameter_case_variants_override_inherited_values(self):
        with tempfile.TemporaryDirectory() as folder:
            package = "RSDragonwilds/Content/M_Standard_Env_MR_Emissive"
            write_json(Path(folder)/(package+".json"), [
                {"Type": "Material", "Name": "M_Standard_Env_MR_Emissive", "CachedExpressionData": {
                    "RuntimeEntries": {"ParameterInfoSet": [{"Name": "Emissive Scale"}]},
                    "ScalarValues": [1],
                    "RuntimeEntries[8]": {"ParameterInfoSet": [{"Name": "CanBeEmissive"}]},
                    "StaticSwitchValues": [True]}},
                {"Name": "Child", "Properties": {"Parent": {"ObjectPath": package+".0"},
                    "ScalarParameterValues": [{"ParameterInfo": {"Name": "Emissive scale"}, "ParameterValue": 0}],
                    "StaticParametersRuntime": {"StaticSwitchParameters": [
                        {"ParameterInfo": {"Name": "canbeemissive"}, "Value": False, "bOverride": True}]}}}])
            params = material_parameters(Resolver(folder), package+".1")
            self.assertEqual(params["scalars"], {"Emissive Scale": 0})
            self.assertEqual(params["switches"], {"CanBeEmissive": False})

    def test_explicit_base_color_beats_earlier_moss_overlay(self):
        params = {"textures": {"Moss Base Colour": "Moss_BC.0", "BaseColor": "Cloth_BCC.0", "Normal": "Cloth_NRM.0"}}
        self.assertEqual(texture_roles(params), {"base_color": "Cloth_BCC.0", "normal": "Cloth_NRM.0"})

    def test_disabled_fur_texture_and_white_mountain_constant_are_not_missing_maps(self):
        params = {"master": "Test/M_ShellFur.0", "textures": {"BaseTexture": "Unused_D.0"}, "switches": {"UseTexture?": False}}
        self.assertNotIn("base_color", texture_roles(params))
        params["switches"]["UseTexture?"] = True
        self.assertEqual(texture_roles(params)["base_color"], "Unused_D.0")
        params = {"master": "Test/M_Standard_Env_MR_BackgroundMountains.0", "textures": {"BaseColor": "Test/T_Default_White_D.0"}}
        self.assertNotIn("base_color", texture_roles(params))
        self.assertEqual(render_settings(params)["base_color_parameter"], "Color Base")
        self.assertEqual(render_settings(params)["family"], "surface_maps")

    def test_world_shader_surface_roles_do_not_use_overlay_as_base(self):
        params = {"master": "P/M_dreamstone.0", "textures": {"BC": "Stone_BC.0", "N": "Stone_N.0"}}
        self.assertEqual(texture_roles(params), {"base_color": "Stone_BC.0", "normal": "Stone_N.0"})
        params = {"master": "P/M_Basic_VertPaint.0", "textures": {
            "Vert B BC": "Overlay_BC.0", "Vert Base BC": "Base_BCC.0", "Vert Base NRM": "Base_NRM.0"}}
        self.assertEqual(texture_roles(params), {"base_color": "Base_BCC.0", "normal": "Base_NRM.0"})
        params = {"master": "P/M_Water.0", "textures": {"Duckweed BC": "Duckweed_BC.0", "NormalMap": "Water_N.0"}}
        self.assertEqual(texture_roles(params), {"normal": "Water_N.0"})

    def test_foliage_vertex_channels_are_not_diffuse_colors(self):
        p = {"master": "RSDragonwilds/Content/Materials/Environment/M_Grass.0", "switches": {}}
        self.assertEqual(render_settings(p)["vertex_color_mode"], "ignore")
        p["switches"]["AO from VertexCol Red"] = True
        self.assertEqual(render_settings(p)["vertex_color_mode"], "ao_red")
        self.assertEqual(render_settings({"master": "Unrelated.0", "switches": {}}), {})

    def test_hardwired_material_references_are_retained_and_inherited(self):
        with tempfile.TemporaryDirectory() as folder:
            package = "RSDragonwilds/Content/Hardwired"
            ref = {"ObjectPath": "RSDragonwilds/Content/Texture.0"}
            write_json(Path(folder)/(package+".json"), [
                {"Type": "Material", "Name": "Hardwired", "ReferencedTextures": [ref],
                 "CachedExpressionData": {"ReferencedTextures": [ref]}},
                {"Name": "Child", "Properties": {"Parent": {"ObjectPath": package+".0"}}}])
            params = material_parameters(Resolver(folder), package+".1")
            self.assertEqual(params["referenced_textures"], [ref["ObjectPath"]])

    def test_geyser_hardwired_roles_are_explicit_not_guessed(self):
        refs = ["RSDragonwilds/Content/Textures/"+name+".0" for name in GEYSER_TEXTURES.values()]
        params = {"master": GEYSER_MASTER, "textures": {}, "referenced_textures": refs}
        self.assertEqual(texture_roles(params), dict(zip(GEYSER_TEXTURES, refs)))
        self.assertTrue(render_settings(params)["emissive_enabled"])
        self.assertEqual(render_settings(params)["family"], "geyser_stones")
        params["master"] = "UnknownShader.0"
        self.assertEqual(texture_roles(params), {})
        params["master"] = GEYSER_MASTER
        params["referenced_textures"].append("Other/"+GEYSER_TEXTURES["base_color"]+".0")
        self.assertNotIn("base_color", texture_roles(params))

    def material_audit_fixture(self, folder):
        root = Path(folder)
        write_json(root/"model.gltf", {"meshes": [{"primitives": [{"material": 0}]}],
            "materials": [{"emissiveFactor": [1, 1, 1], "emissiveTexture": {"index": 0}}],
            "textures": [{"extensions": {"EXT_texture_webp": {"source": 0}}}],
            "images": [{"uri": "textures/color.webp"}]})
        write_json(root/"WebAssetManifest.json", {"entries": {"mesh": {
            "used_textures": ["Texture/Color.png -> textures/color.webp"]}}})
        asset = {"id": "a", "source": "Mesh.0", "path": "model.gltf", "root": "models", "native": False,
                 "materials": [], "default_materials": ["M.0", "Unused.0"], "section_material_slots": [0]}
        scene = {"version": "test", "assets": [asset], "placements": [{"id": "p", "asset": "a", "matrix": np.eye(4).tolist()}],
                 "materials": {"M.0": {"settings": {"emissive_enabled": True},
                 "textures": {"base_color": "color.webp", "emissive": "glow.webp"}}}}
        params = {"M.0": {"roles": {"base_color": "Texture/Color.0", "emissive": "Texture/Glow.0"}}}
        return root, scene, params

    def test_audit_detects_missing_base_and_wrong_emission_texture(self):
        with tempfile.TemporaryDirectory() as folder:
            root, scene, params = self.material_audit_fixture(folder)
            audit = audit_materials(scene, params, root)
            self.assertEqual(audit["summary"]["materials"], 1)
            row = audit["materials"][0]
            self.assertEqual(row["status"], "corrected_in_preview")
            self.assertEqual({i["code"] for i in row["issues"]}, {"upstream_texture_missing", "upstream_texture_mismatch"})
            self.assertEqual(row["placement_count"], 1)
            scene["materials"]["M.0"]["settings"] = {}
            self.assertEqual(audit_materials(scene, params, root)["materials"][0]["status"], "needs_review")

    def test_audit_distinguishes_unavailable_role_from_unverified_shader(self):
        with tempfile.TemporaryDirectory() as folder:
            root, scene, params = self.material_audit_fixture(folder)
            scene["materials"]["M.0"]["texture_gaps"] = [{"role": "base_color", "source": "Array.0",
                                                          "reason": "Texture array needs a shader adapter"}]
            row = audit_materials(scene, params, root)["materials"][0]
            self.assertEqual(row["status"], "conversion_gap")
            self.assertIn("texture_role_unavailable", {i["code"] for i in row["issues"]})

    def test_audit_uses_effective_slots_and_ignores_baked_roads(self):
        with tempfile.TemporaryDirectory() as folder:
            root, scene, params = self.material_audit_fixture(folder)
            scene["assets"][0]["materials"] = ["Constant.0"]
            scene["materials"]["Constant.0"] = {"textures": {}, "settings": {}}
            params["Constant.0"] = {"vectors": {"BaseColor": {"R": 1, "G": 1, "B": 1}}}
            audit = audit_materials(scene, params, root)
            self.assertEqual(audit["materials"][0]["material"], "Constant.0")
            self.assertEqual(audit["materials"][0]["issues"], [])
            scene["placements"][0]["baked_road"] = True
            self.assertEqual(audit_materials(scene, params, root)["summary"]["materials"], 0)

    def test_audit_marks_unknown_hardwired_shader_for_review_not_broken(self):
        with tempfile.TemporaryDirectory() as folder:
            root, scene, params = self.material_audit_fixture(folder)
            params["M.0"] = {"referenced_textures": ["Texture/Unknown.0"]}
            row = audit_materials(scene, params, root)["materials"][0]
            self.assertEqual(row["status"], "needs_review")
            self.assertEqual(row["issues"][0]["code"], "hardwired_shader_unmapped")


if __name__=='__main__':
    unittest.main()
