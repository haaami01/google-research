// Copyright 2023 The Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef FAIR_SUBMODULAR_MATROID_UNIFORM_MATROID_H_
#define FAIR_SUBMODULAR_MATROID_UNIFORM_MATROID_H_

#include <memory>
#include <set>
#include <vector>

#include "matroid.h"

class UniformMatroid : public Matroid {
 public:
  explicit UniformMatroid(int k);

  ~UniformMatroid() override = default;

  // Reset to empty set.
  void Reset() override;

  // Return whether adding an element would be feasible.
  bool CanAdd(int element) const override;

  // Return whether add element while removing anothe one would be feasible.
  bool CanSwap(int element, int swap) const override;

  // Returns all possible swaps for a given new element.
  std::vector<int> GetAllSwaps(int elements) const override;

  // Add an element. Assumes that the element can be added.
  void Add(int element) override;

  // Removes the element.
  void Remove(int element) override;

  // Checks if a set is feasible.
  bool IsFeasible(const std::vector<int>& elements) const override;

  // Checks whether the current set is feasible.
  bool CurrentIsFeasible() const override;

  // Return the current set.
  std::vector<int> GetCurrent() const override;

  // Returns whether an element is in the current set.
  bool InCurrent(int element) const override;

  // Clone the object.
  std::unique_ptr<Matroid> Clone() const override;

 private:
  // The current set of elements.
  std::set<int> current_set_;
  // The cardinality constraint.
  const int k_;
};

#endif  // FAIR_SUBMODULAR_MATROID_UNIFORM_MATROID_H_
