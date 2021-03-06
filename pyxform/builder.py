import os
import copy

import utils
import file_utils
from question import Question, InputQuestion, TriggerQuestion, \
    UploadQuestion, MultipleChoiceQuestion
from section import RepeatingSection, GroupedSection
from survey import Survey
from xls2json import SurveyReader
from question_type_dictionary import QUESTION_TYPE_DICT
from errors import PyXFormError
from pyxform import constants
from pyxform import aliases


def copy_json_dict(json_dict):
    """
    Returns a deep copy of the input json_dict
    """
    json_dict_copy = None
    items = None
    
    if type(json_dict) is list:
        json_dict_copy = [None]*len(json_dict)
        items = enumerate(json_dict)
    elif type(json_dict) is dict:
        json_dict_copy = {}
        items = json_dict.items()
    
    for key, value in items:
        if type(value) is dict or type(value) is list:
            json_dict_copy[key] = copy_json_dict(value)
        else:
            json_dict_copy[key] = value
            
    return json_dict_copy

class SurveyElementBuilder(object):
    # we use this CLASSES dict to create questions from dictionaries
    QUESTION_CLASSES = {
        u"": Question,
        u"input": InputQuestion,
        u"trigger": TriggerQuestion,
        constants.SELECT_ONE_XFORM: MultipleChoiceQuestion,
        constants.SELECT_ALL_THAT_APPLY_XFORM: MultipleChoiceQuestion,
        u"upload": UploadQuestion,
        }

    SECTION_CLASSES = {
        u"group": GroupedSection,
        u"repeat": RepeatingSection,
        u"survey": Survey,
        }

    def __init__(self, **kwargs):
        self._add_none_option = False #I don't know why we would need an explicit none option for select alls
        self.set_sections(
            kwargs.get(u"sections", {})
            )

    def set_sections(self, sections):
        """
        sections is a dict of python objects, a key in this dict is
        the name of the section and the value is a dict that can be
        used to create a whole survey.
        """
        assert type(sections) == dict
        self._sections = sections

    def create_survey_element_from_dict(self, element_dict):
        """
        Convert from a nested python dictionary/array structure
        (a json dict I call it because it corresponds directly with a json object)
        to a survey object
        """
        if u"add_none_option" in element_dict:
            self._add_none_option = element_dict[u"add_none_option"]
        if element_dict[u"type"] in self.SECTION_CLASSES:
            return self._create_section_from_dict(element_dict)
        elif element_dict[u"type"] == u"loop":
            return self._create_loop_from_dict(element_dict)
        elif element_dict[u"type"] == u"include":
            section_name = element_dict[constants.NAME]
            if section_name not in self._sections:
                raise PyXFormError("This section has not been included.",
                                section_name, self._sections.keys())
            element_dict = self._sections[section_name]
            full_survey = self.create_survey_element_from_dict(element_dict)
            return full_survey.children
        else:
            question_type_dict_copy= copy_json_dict(QUESTION_TYPE_DICT) # FIXME: Why do we need a copy of this?
            return self._create_question_from_dict(element_dict, question_type_dict_copy, self._add_none_option)

    @staticmethod
    def _create_question_from_dict(question_dict, question_type_dictionary, add_none_option=False):
        question_type_str = question_dict[constants.TYPE]
        question_dict_copy = question_dict.copy()
        
        # TODO: Keep add none option?
        if add_none_option and question_type_str.startswith(u"select all that apply"):
            SurveyElementBuilder._add_none_option_to_select_all_that_apply(question_dict_copy)

        # Handle or_other on select type questions
        or_other_str = u" or specify other"
        if question_type_str.endswith(or_other_str):
            question_type_str = question_type_str[:len(question_type_str) - len(or_other_str)]
            question_dict_copy["type"] = question_type_str
            SurveyElementBuilder._add_other_option_to_multiple_choice_question(question_dict_copy)
            return [SurveyElementBuilder._create_question_from_dict(question_dict_copy, question_type_dictionary, add_none_option),
                    SurveyElementBuilder._create_specify_other_question_from_dict(question_dict_copy)]
        
        question_class = SurveyElementBuilder._get_question_class(question_type_str, question_type_dictionary)
        
        # De-alias multiple-choice question types.
        if question_type_str in aliases.multiple_choice:
            question_type_str= aliases.multiple_choice[question_type_str]
            question_dict_copy["type"] = question_type_str
            
        # todo: clean up this spaghetti code
        question_dict_copy[u"question_type_dictionary"] = question_type_dictionary
        if question_class:
            return question_class(**question_dict_copy)
        return []
    
    @staticmethod
    def _add_other_option_to_multiple_choice_question(question_dict):
        # ideally, we'question_dict just be pulling from children
        choice_list = question_dict.get(u"choices", question_dict.get(u"children", []))
        if len(choice_list) <= 0:
            raise PyXFormError("There should be choices for this question.")
        other_choice = {
            constants.NAME: u"other",
            u"label": u"Other",
            }
        if other_choice not in choice_list:
            choice_list.append(other_choice)

    @staticmethod
    def _add_none_option_to_select_all_that_apply(question_dict_copy):
        choice_list = question_dict_copy.get(u"choices", question_dict_copy.get(u"children", []))
        if len(choice_list) <= 0:
            raise PyXFormError("There should be choices for this question.")
        none_choice = {
            constants.NAME: u"none",
            u"label": u"None",
            }
        if none_choice not in choice_list:
            choice_list.append(none_choice)
            none_constraint = u"(.='none' or not(selected(., 'none')))"
            if constants.BIND not in question_dict_copy:
                question_dict_copy[constants.BIND] = {}
            if u"constraint" in question_dict_copy[constants.BIND]:
                question_dict_copy[constants.BIND][u"constraint"] += " and " + none_constraint
            else:
                question_dict_copy[constants.BIND][u"constraint"] = none_constraint

    @staticmethod
    def _get_question_class(question_type_str, question_type_dictionary):
        """
        Read the type string from the json format,
        and find what class it maps to going through type_dictionary -> QUESTION_CLASSES 
        """
        if question_type_str in question_type_dictionary:
            question_type = question_type_dictionary[question_type_str]
        # De-alias multiple-choice question types.
        elif question_type_str in aliases.multiple_choice:
            question_type= question_type_dictionary[ aliases.multiple_choice[question_type_str] ]
        else:
            question_type= dict() # ...
        
        control_dict = question_type.get(u"control", {})
        control_tag = control_dict.get(u"tag", u"")
        return SurveyElementBuilder.QUESTION_CLASSES[control_tag]
    
    @staticmethod
    def _create_specify_other_question_from_dict(d):
        kwargs = {
            u"type": u"text",
            constants.NAME: u"%s_other" % d[constants.NAME],
            u"label": u"Specify other.",
            constants.BIND: {u"relevant": u"selected(../%s, 'other')" % d[constants.NAME]},
            }
        return InputQuestion(**kwargs)

    def _create_section_from_dict(self, section_dict):
        section_dict_copy = section_dict.copy()
        children = section_dict_copy.pop(u"children", [])
        section_class = self.SECTION_CLASSES[section_dict_copy[u"type"]]
        if section_dict[u'type'] == u'survey' and constants.TITLE not in section_dict:
            section_dict_copy[constants.TITLE] = section_dict[constants.NAME]
        result = section_class(**section_dict_copy)
        for child in children:
            #Deep copying the child is a hacky solution to the or_other bug.
            #I don't know why it works.
            #And I hope it doesn't break something else.
            #I think the good solution would be to rewrite this class.
            survey_element = self.create_survey_element_from_dict(copy.deepcopy(child))
            if survey_element:
                result.add_children(survey_element)
        return result

    def _create_loop_from_dict(self, d, group_each_iteration=True):
        """
        Takes a json_dict of "loop" type
        Returns a GroupedSection
        """
        d_copy = d.copy()
        children = d_copy.pop(u"children", [])
        columns = d_copy.pop(u"columns", [])
        result = GroupedSection(**d_copy)

        # columns is a left over from when this was
        # create_table_from_dict, I will need to clean this up
        for column_dict in columns:
            # If this is a none option for a select all that apply
            # question then we should skip adding it to the result
            if column_dict[constants.NAME] == "none": continue

            column = GroupedSection(**column_dict)
            for child in children:
                question_dict = self._name_and_label_substitutions(child, column_dict)
                question = self.create_survey_element_from_dict(question_dict)
                column.add_child(question)
            result.add_child(column)
        if result.name != u"":
            return result
        return result.children #TODO: Verify that nothing breaks if this returns a list

    def _name_and_label_substitutions(self, question_template, column_headers):
        # if the label in column_headers has multiple languages setup a
        # dictionary by language to do substitutions.
        if type(column_headers[u"label"]) == dict:
            info_by_lang = dict(
                [(lang, {constants.NAME: column_headers[constants.NAME], u"label": column_headers[u"label"][lang]}) for lang in column_headers[u"label"].keys()]
                )

        result = question_template.copy()
        for key in result.keys():
            if type(result[key]) == unicode:
                result[key] = result[key] % column_headers
            elif type(result[key]) == dict:
                result[key] = result[key].copy()
                for key2 in result[key].keys():
                    if type(column_headers[u"label"]) == dict:
                        result[key][key2] = result[key][key2] % info_by_lang.get(key2, column_headers)
                    else:
                        result[key][key2] = result[key][key2] % column_headers
        return result

    def create_survey_element_from_json(self, str_or_path):
        d = utils.get_pyobj_from_json(str_or_path)
        return self.create_survey_element_from_dict(d)


def create_survey_element_from_dict(d, sections={}):
    """
    Creates a Survey from a dictionary in the format provided by SurveyReader
    """
    builder = SurveyElementBuilder()
    builder.set_sections(sections)
    return builder.create_survey_element_from_dict(d)


def create_survey_element_from_json(str_or_path):
    survey_dict = utils.get_pyobj_from_json(str_or_path)
    return create_survey_element_from_dict(survey_dict)


def create_survey_from_xls(path_or_file):
    excel_reader = SurveyReader(path_or_file)
    d = excel_reader.to_json_dict()
    survey = create_survey_element_from_dict(d)
    if not survey.id_string:
        survey.id_string = excel_reader._name
    return survey


def create_survey(
    name_of_main_section=None, sections={},
    main_section=None,
    id_string=None,
    title=None,
    default_language=None,
    question_type_dictionary=None
    ):
    """
    name_of_main_section -- a string key used to find the main section in the sections dict if it is not supplied in the main_section arg
    main_section -- a json dict that represents a survey
    sections -- a dictionary of sections that can be drawn from to build the survey
    This function uses the builder class to create and return a survey.
    """
    if main_section == None:
        main_section = sections[name_of_main_section]
    builder = SurveyElementBuilder()
    builder.set_sections(sections)

    #assert name_of_main_section in sections, name_of_main_section
    if constants.ID_STRING not in main_section:
        main_section[constants.ID_STRING] = name_of_main_section if id_string is None else name_of_main_section
    survey = builder.create_survey_element_from_dict(main_section)
    
    # not sure where to do this without repeating ourselves, but it's needed to pass
    # xls2xform tests
    # TODO: I would assume that the json-dict is valid (i.e. that it has a id string), then throw an error here.
    #        We can set the id to whatever we want in xls2json.
    #        Although to be totally modular, maybe we do need to repeat a lot of the validation and setting default value stuff from xls2json
    if id_string is not None:
        survey.id_string = id_string

    if title is not None:
        survey.title = title
    survey.def_lang = default_language
    return survey


def create_survey_from_path(path, include_directory=False):
    """
    include_directory -- Switch to indicate that all the survey forms in
                         the same directory as the specified file should be read
                         so they can be included through include types.
    @see: create_survey
    """
    directory, file_name = os.path.split(path)
    main_section_name = file_utils._section_name(file_name)
    if include_directory:
        main_section_name = file_utils._section_name(file_name)
        sections = file_utils.collect_compatible_files_in_directory(directory)
    else:
        main_section_name, section = file_utils.load_file_to_dict(path)
        sections = {main_section_name: section}
    pkg = {
        u'name_of_main_section': main_section_name,
        u'sections': sections
        }
    return create_survey(**pkg)
