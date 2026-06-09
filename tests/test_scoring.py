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

    def test_winner_plus_one_team_goals_gets_four(self):
        self.assertEqual(score_prediction(prediction(2, 0), match(2, 1)), 4)

    def test_winner_only_gets_three(self):
        self.assertEqual(score_prediction(prediction(1, 0), match(3, 1)), 3)

    def test_one_team_goals_without_winner_gets_one(self):
        self.assertEqual(score_prediction(prediction(2, 1), match(0, 1)), 1)

    def test_draw_exact_gets_six(self):
        self.assertEqual(score_prediction(prediction(1, 1), match(1, 1)), 6)

    def test_draw_without_exact_gets_three(self):
        self.assertEqual(score_prediction(prediction(0, 0), match(2, 2)), 3)

    def test_knockout_exact_draw_and_penalty_winner_gets_nine(self):
        self.assertEqual(
            score_prediction(prediction(1, 1, "A"), match(1, 1, phase_slug="oitavas", penalty_winner="A")),
            9,
        )

    def test_knockout_exact_draw_wrong_penalty_gets_six(self):
        self.assertEqual(
            score_prediction(prediction(1, 1, "A"), match(1, 1, phase_slug="oitavas", penalty_winner="B")),
            6,
        )

    def test_knockout_draw_wrong_score_correct_penalty_gets_six(self):
        self.assertEqual(
            score_prediction(prediction(0, 0, "B"), match(2, 2, phase_slug="oitavas", penalty_winner="B")),
            6,
        )

    def test_knockout_draw_wrong_score_wrong_penalty_gets_three(self):
        self.assertEqual(
            score_prediction(prediction(0, 0, "A"), match(2, 2, phase_slug="oitavas", penalty_winner="B")),
            3,
        )


if __name__ == "__main__":
    unittest.main()

