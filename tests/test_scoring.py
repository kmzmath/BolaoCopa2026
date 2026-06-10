import os
import unittest

os.environ["BOLAO_SKIP_INIT"] = "1"

from app import score_prediction


def match(home, away, phase_slug="grupos", penalty_winner=None):
    return {
        "phase_slug": phase_slug,
        "result_home": home,
        "result_away": away,
        "penalty_winner": penalty_winner,
    }


def prediction(home, away, advances=None):
    return {"home_score": home, "away_score": away, "advances": advances}


class ScoringTest(unittest.TestCase):
    def test_exact_score_gets_six(self):
        self.assertEqual(score_prediction(prediction(2, 1), match(2, 1)), 6)

    def test_result_plus_goal_difference_gets_four(self):
        self.assertEqual(score_prediction(prediction(2, 0), match(3, 1)), 4)

    def test_draw_plus_goal_difference_gets_four(self):
        self.assertEqual(score_prediction(prediction(0, 0), match(2, 2)), 4)

    def test_result_plus_one_team_goals_gets_three(self):
        self.assertEqual(score_prediction(prediction(2, 0), match(2, 1)), 3)

    def test_result_only_gets_two(self):
        self.assertEqual(score_prediction(prediction(2, 0), match(4, 3)), 2)

    def test_inverted_score_gets_minus_two(self):
        self.assertEqual(score_prediction(prediction(2, 0), match(0, 2)), -2)

    def test_wrong_score_gets_zero(self):
        self.assertEqual(score_prediction(prediction(2, 0), match(1, 1)), 0)

    def test_draw_exact_gets_six(self):
        self.assertEqual(score_prediction(prediction(1, 1), match(1, 1)), 6)

    def test_knockout_ignores_penalty_winner(self):
        self.assertEqual(
            score_prediction(prediction(1, 1, "A"), match(1, 1, phase_slug="oitavas", penalty_winner="B")),
            6,
        )

    def test_knockout_draw_without_exact_uses_regular_rules(self):
        self.assertEqual(
            score_prediction(prediction(0, 0, "A"), match(2, 2, phase_slug="oitavas", penalty_winner="B")),
            4,
        )


if __name__ == "__main__":
    unittest.main()
