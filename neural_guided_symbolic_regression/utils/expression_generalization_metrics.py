# coding=utf-8
# Copyright 2023 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Library of expression generalization metrics.

Metrics are evaluated per condition (i.e., a given set of symbolic properties
and their values). We are interested in the following metrics:
 * Success rate: If we generate, let's say 100, expressions, how many of
   them satisfy the condition. In formula, it is number of expressions
   satisfying the condition / number of expressions generated.
 * Syntactic novelty rate: Among all expressions satisfying the condition, how
   many of them haven't been seen in the training set. In formula, it is number
   of unseen expressions satisfying the condition / number of expressions
   satisfying the condition. Note that 'x' and '( x )' are syntactically
   different. So if 'x' appears in generation, and '( x )' appears in train, 'x'
   is still regarded as a syntactic novelty.
 * Semantic novelty rate: Among all expressions satisfying the condition, how
   many expressions having simplified expressions that haven't been seen in the
   simplified expressions derived from the training set. In formula, it is
   number of expressions with unseen simplified expressions satisfying the
   condition / number of expressions satisfying the condition. If 'x' appears in
   generation, and '( x )' appears in train, 'x' would not be counted as a
   semantic novelty.
 Note that the above three metrics all have "unique" versions by adding a
 "unique" operation while counting the numbers.
 Note that the last two rates would be always one for conditions not contained
 in the training set but contained in the generation because everything
 generated would be novel for the training set.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import itertools
import numpy as np
import sympy

SeenAndUnseenExpressions = collections.namedtuple(
    'SeenAndUnseenExpressions',
    ['syntactic_novelty', 'semantic_novelty'])
NoveltySummary = collections.namedtuple(
    'NoveltySummary',
    ['num_seen', 'num_unseen', 'novelty_rate'])


def combine_list_values_in_dict(expression_dict):
  """Combines list values in a dictionary into a single list.

  Args:
    expression_dict: A dictionary, where each key is a simplified expression and
      the value is a list/Numpy array of expressions with the simplified
      expression.

  Returns:
    List of all expressions combined from the values of the input dictionary.
  """
  return list(itertools.chain(*expression_dict.values()))


def get_seen_and_unseen_expressions(generated_expressions,
                                    training_expressions,
                                    deduplicate_unseen):
  """Gets seen and unseen expressions.

  This function outputs two types of "unseen" expressions. One is the expression
  that does not appear in the training set (i.e., syntactic novelty), the other
  is the expression whose simplified expression does not appear in the
  simplified expressions derived from the training set (i.e., semantic novelty).

  Args:
    generated_expressions: A dictionary, where each key is a simplified
      expression of expressions generated by some model and the value is a
      list/Numpy array of expressions with the simplified expression.
    training_expressions:  A dictionary, where each key is a simplified
      expression of expressions in the training set and the value is a
      list/Numpy array of expressions with the simplified expression.
    deduplicate_unseen: (Boolean) Whether we remove duplicates from the unseen
      expressions. For syntactic novelty, the duplicates are defined as whether
      two expressions are the same. For semantic novelty, the duplicates are
      defined as whether the simplified expressions of two expressions are the
      same.

  Returns:
    A namedtuple containing the following fields:
      * syntactic_novelty: a 2-tuple of lists, where the first list contains the
        generated expressions that appear in the training set, and the second
        list contains the generated expressions that do not appear in the
        training set.
      * semantic_novelty: a 2-tuple of lists, where the first list contains the
        generated expressions whose simplified expressions appear in the
        simplified expressions derived from the training set, and the second
        list contains those whose simplified expressions do not appear in the
        simplified expressions.
  """
  semantic_seen_expressions, semantic_unseen_expressions = [], []
  all_generated_expressions = combine_list_values_in_dict(generated_expressions)
  all_training_expressions_set = set(
      combine_list_values_in_dict(training_expressions))
  syntactic_seen_expressions = [
      expression for expression in all_generated_expressions
      if expression in all_training_expressions_set]
  syntactic_unseen_expressions = [
      expression for expression in all_generated_expressions
      if expression not in all_training_expressions_set]
  for simplified_expression, expressions in generated_expressions.items():
    expressions = list(expressions)
    if simplified_expression in training_expressions:
      semantic_seen_expressions.extend(expressions)
    else:
      semantic_unseen_expressions.extend(expressions)

  # Correct for the case when the simplified expressions of the same expression
  # in generation and train are different.
  # This does not solve the problem when there are two different expressions in
  # generation and train which are supposed to have the same simplified
  # expression, but actually they are not due to the instability of
  # sympy.simplify.
  corrected_semantic_unseen_expressions = []
  for expression in semantic_unseen_expressions:
    if expression in all_training_expressions_set:
      semantic_seen_expressions.append(expression)
    else:
      corrected_semantic_unseen_expressions.append(expression)
  semantic_unseen_expressions = corrected_semantic_unseen_expressions

  if deduplicate_unseen:
    syntactic_unseen_expressions = list(set(syntactic_unseen_expressions))
    existing_simplified_expressions = set()
    deduplicate_semantic_unseen_expressions = []
    for expression in semantic_unseen_expressions:
      # We can afford to run simplify again here since there would be not many
      # elements in semantic_unseen_expressions.
      simplified_expression = str(sympy.simplify(expression))
      if simplified_expression not in existing_simplified_expressions:
        existing_simplified_expressions.add(simplified_expression)
        deduplicate_semantic_unseen_expressions.append(expression)
    semantic_unseen_expressions = deduplicate_semantic_unseen_expressions

  seen_and_unseen_expressions = SeenAndUnseenExpressions(
      syntactic_novelty=(syntactic_seen_expressions,
                         syntactic_unseen_expressions),
      semantic_novelty=(semantic_seen_expressions,
                        semantic_unseen_expressions))
  return seen_and_unseen_expressions


def get_novelty_rate(seen_expressions, unseen_expressions):
  """Gets novelty rate.

  The definition of novelty rate is described in the docstring of the file. This
  function is written separately from the function
  get_seen_and_unseen_expressions so that one may check the detailed expressions
  instead of just numbers.

  Args:
    seen_expressions: A list/Numpy array of seen expressions.
    unseen_expressions: A list/Numpy array of unseen expressions.

  Returns:
    A namedtuple containing the following fields:
      * num_seen: Integer, number of seen expressions.
      * num_unseen: Integer, number of unseen expressions.
      * novelty_rate: Float, novelty rate, which is the ratio between num_unseen
        and total number.

  Raises:
    ValueError: Total number of expressions cannot be zero.
  """
  num_seen_expressions = len(seen_expressions)
  num_unseen_expressions = len(unseen_expressions)
  num_total_expressions = num_seen_expressions + num_unseen_expressions
  if num_total_expressions == 0:
    raise ValueError('Total number of expressions cannot be zero.')
  novelty_summary = NoveltySummary(
      num_seen=num_seen_expressions,
      num_unseen=num_unseen_expressions,
      novelty_rate=float(num_unseen_expressions) / num_total_expressions)
  return novelty_summary


def get_distance_from_expected_condition(expression_df,
                                         distance_for_nonterminal=99,
                                         distance_for_sympy_failure=None):
  """Gets distance of true condition from expected condition.

  For each expected condition, we generate, let's say, 100 expressions and
  compute their true asymptotic conditions. We measure the goodness of the
  generation at this condition by the mean of the L1-distances between true
  conditions and the condition. The ideal case is all the 100 generated
  expressions have the expected condition so the metric is exactly zero.

  Note that there are NaN's in true_leading_at_0 and true_leading_at_inf due to
  non-terminal expressions or sympy failure of evaluating asymptotic
  conditions. This function can replace the NaN's by user provided distance.

  Args:
    expression_df: A Pandas dataframe of generated expressions with each row
      corresponding to an expression. It should have columns true_leading_at_0,
      true_leading_at_inf, expected_leading_at_0, expected_leading_at_inf, and
      is_terminal.
    distance_for_nonterminal: Integer, user specified distance between the true
      condition of a non-terminal expression and its expected condition. Note
      that if an expression is not terminal, its true condition is NaN.
    distance_for_sympy_failure: Integer, user specified distance between the
      true condition of a terminal expression (that fails to be evaluated by
      sympy for asymptotic conditions) and its expected condition. Note that if
      an expression fails to be evaluated by sympy, its true condition is NaN.
      If None, simply ignore these expressions while computing the mean
      distance of the expected condition.

  Returns:
    A Pandas dataframe of distance from expected condition with each row
    corresponding to an expected condition. It should have columns
    expected_leading_at_0, expected_leading_at_inf and
    distance_from_expected_condition.

  """
  expression_df['distance_from_expected_condition'] = (
      np.abs(expression_df['expected_leading_at_0'] -
             expression_df['true_leading_at_0']) +
      np.abs(expression_df['expected_leading_at_inf'] -
             expression_df['true_leading_at_inf']))
  expression_df.loc[
      expression_df['is_terminal'] ==
      0, 'distance_from_expected_condition'] = distance_for_nonterminal
  if distance_for_sympy_failure is not None:
    expression_df['distance_from_expected_condition'] = expression_df[
        'distance_from_expected_condition'].fillna(
            value=distance_for_sympy_failure)
  distance_from_expected_condition_df = expression_df.groupby(
      by=['expected_leading_at_0', 'expected_leading_at_inf'
         ])['distance_from_expected_condition'].mean().to_frame(
             'distance_from_expected_condition').reset_index()
  return distance_from_expected_condition_df
