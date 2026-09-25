from django.test import SimpleTestCase

from plane.db.models import CycleUserProperties, ModuleUserProperties, ProjectMember, ProjectUserProperty


class TestDefaultBoardLayout(SimpleTestCase):
    def test_new_views_open_as_status_columns(self):
        for model in (ProjectUserProperty, CycleUserProperties, ModuleUserProperties):
            with self.subTest(model=model.__name__):
                filters = model().display_filters
                self.assertEqual(filters["layout"], "kanban")
                self.assertEqual(filters["group_by"], "state")

    def test_member_defaults_open_as_status_columns(self):
        member = ProjectMember()
        for props in (member.view_props, member.default_props):
            self.assertEqual(props["display_filters"]["layout"], "kanban")
            self.assertEqual(props["display_filters"]["group_by"], "state")

    def test_saved_layout_is_preserved(self):
        for model in (ProjectUserProperty, CycleUserProperties, ModuleUserProperties):
            with self.subTest(model=model.__name__):
                filters = {"layout": "list", "group_by": None}
                self.assertEqual(model(display_filters=filters).display_filters, filters)
